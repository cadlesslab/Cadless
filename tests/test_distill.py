"""Auto-distill flywheel tests.

The flywheel writes a KB entry whenever a turn settles ok+asserted. The embedding
is computed over the NL intent + a structural feature-tag signature (NOT the raw
build123d code); the raw code is still stored. Distill is best-effort: a failure
must never propagate to the caller's turn.

Uses ``asyncio.run`` + temp dirs (like ``test_store.py``) and the offline fake
provider's deterministic ``embed`` so no AWS/network is needed.
"""

import asyncio
from pathlib import Path

from cadless.config import settings as base_settings
from cadless.distill import (
    auto_distill,
    distill,
    feature_tags,
    signature_text,
)
from cadless.llm.providers.fake import FakeChatProvider
from cadless.store import Store

_CODE = """
length = 40
result = Box(length, 20, 5)
result = fillet(result.edges(), radius=2)
hole = Cylinder(3, 10)
result = result - hole
"""


def _store(tmp_path: Path) -> Store:
    return Store(db_path=tmp_path / "db.sqlite", artifacts_dir=tmp_path / "artifacts")


def _provider():
    cfg = base_settings.model_copy(update={"embed_dimensions": 64})
    return FakeChatProvider(config=cfg)


def run(coro):
    return asyncio.run(coro)


# ---- structural feature-tag signature -------------------------------------


def test_feature_tags_extracts_primitives_and_operations():
    """Tags include build123d primitives + operations used and geometry tags."""
    tags = feature_tags(_CODE, {"bbox": [40, 20, 5], "volume": 4000.0})
    # primitives used
    assert "primitive:box" in tags
    assert "primitive:cylinder" in tags
    # operations used
    assert "op:fillet" in tags
    assert "op:subtract" in tags
    # geometry-derived tags from the metrics signature
    assert any(t.startswith("parts:") or t.startswith("bbox:") for t in tags)
    # deterministic + sorted (stable embedding text)
    assert tags == sorted(tags)
    assert feature_tags(_CODE, {"bbox": [40, 20, 5]}) == feature_tags(_CODE, {"bbox": [40, 20, 5]})


def test_signature_text_combines_intent_and_tags_not_code():
    """The embedded text is intent + feature tags, never the raw code."""
    tags = ["op:fillet", "primitive:box"]
    text = signature_text("a filleted plate", tags)
    assert "a filleted plate" in text
    assert "op:fillet" in text and "primitive:box" in text
    assert "Box(" not in text  # raw code must not leak into the embedded text


# ---- /distill extraction action -------------------------------------------


def test_distill_writes_entry_embedding_intent_signature_code_stored(tmp_path):
    """distill() persists one KB entry: code stored, embedding over intent+signature."""

    async def go():
        s = _store(tmp_path)
        await s.init()
        provider = _provider()
        p = await s.create_project("P")
        v = await s.add_version(p.id, "a plate", _CODE, ok=True)

        entry = await distill(
            s,
            provider,
            project_id=p.id,
            version_id=v.id,
            intent="a filleted plate with a hole",
            code=_CODE,
            metrics={"bbox": [40, 20, 5], "volume": 4000.0, "parameters": {"length": 40}},
        )
        assert entry is not None
        # exactly one entry persisted
        listed = await s.list_kb_entries()
        assert [x.id for x in listed] == [entry.id]
        # raw code is stored on the entry
        assert entry.code == _CODE
        # the embedding equals embed(intent + feature signature), NOT embed(code)
        tags = feature_tags(
            _CODE, {"bbox": [40, 20, 5], "volume": 4000.0, "parameters": {"length": 40}}
        )
        expected = provider.embed(signature_text("a filleted plate with a hole", tags))
        assert entry.embedding == expected
        assert entry.embedding != provider.embed(_CODE)
        # feature tags land in the geometry signature; provenance carries source
        assert entry.geometry_signature.get("feature_tags") == tags
        assert entry.provenance.get("project_id") == p.id
        assert entry.provenance.get("version_id") == v.id

    run(go())


# ---- auto-distill flywheel gate -------------------------------------------


def test_auto_distill_writes_on_ok_turn(tmp_path):
    """An ok turn results in exactly one add_kb_entry."""

    async def go():
        s = _store(tmp_path)
        await s.init()
        provider = _provider()
        p = await s.create_project("P")
        v = await s.add_version(p.id, "a plate", _CODE, ok=True)
        await auto_distill(
            s,
            provider,
            project_id=p.id,
            version_id=v.id,
            intent="a plate",
            code=_CODE,
            ok=True,
            metrics={"bbox": [40, 20, 5]},
        )
        assert len(await s.list_kb_entries()) == 1

    run(go())


def test_auto_distill_skips_failed_turn(tmp_path):
    """A non-ok turn distills nothing."""

    async def go():
        s = _store(tmp_path)
        await s.init()
        provider = _provider()
        p = await s.create_project("P")
        await auto_distill(
            s,
            provider,
            project_id=p.id,
            version_id=None,
            intent="a plate",
            code=_CODE,
            ok=False,
            metrics={"bbox": [40, 20, 5]},
        )
        assert await s.list_kb_entries() == []

    run(go())


def test_auto_distill_skips_failed_assertions_when_required(tmp_path):
    """With require_assertions, a turn whose assertions failed distills nothing."""

    async def go():
        s = _store(tmp_path)
        await s.init()
        provider = _provider()
        p = await s.create_project("P")
        v = await s.add_version(p.id, "a plate", _CODE, ok=True)
        await auto_distill(
            s,
            provider,
            project_id=p.id,
            version_id=v.id,
            intent="a plate",
            code=_CODE,
            ok=True,
            assertions_failed=True,
            require_assertions=True,
            metrics={"bbox": [40, 20, 5]},
        )
        assert await s.list_kb_entries() == []
        # default (require_assertions=False) still distills ok-executing versions
        await auto_distill(
            s,
            provider,
            project_id=p.id,
            version_id=v.id,
            intent="a plate",
            code=_CODE,
            ok=True,
            assertions_failed=True,
            metrics={"bbox": [40, 20, 5]},
        )
        assert len(await s.list_kb_entries()) == 1

    run(go())


def test_auto_distill_swallows_errors_never_raises(tmp_path):
    """A distill failure is best-effort: it must not propagate to the turn."""

    async def go():
        s = _store(tmp_path)
        await s.init()
        p = await s.create_project("P")
        v = await s.add_version(p.id, "a plate", _CODE, ok=True)

        class Boom:
            def embed(self, text):
                raise RuntimeError("embed exploded")

        # must NOT raise despite the provider blowing up
        await auto_distill(
            s,
            Boom(),
            project_id=p.id,
            version_id=v.id,
            intent="a plate",
            code=_CODE,
            ok=True,
            metrics={"bbox": [40, 20, 5]},
        )
        assert await s.list_kb_entries() == []

    run(go())


# ---- providers without embeddings -----------------------------------------


def test_distill_raises_typed_error_when_provider_lacks_embeddings(tmp_path):
    """The direct action surfaces the typed condition — a user explicitly running
    /distill on an embeddings-less provider should see why it cannot work."""
    import pytest

    from cadless.llm.provider import EmbeddingsUnsupported
    from cadless.llm.providers.anthropic import AnthropicChatProvider

    async def go():
        s = _store(tmp_path)
        await s.init()
        p = await s.create_project("P")
        with pytest.raises(EmbeddingsUnsupported):
            await distill(
                s,
                AnthropicChatProvider(config=base_settings),
                project_id=p.id,
                version_id=None,
                intent="a plate",
                code=_CODE,
                metrics={"bbox": [40, 20, 5]},
            )
        assert await s.list_kb_entries() == []

    run(go())


def test_auto_distill_skips_quietly_when_embeddings_unsupported(tmp_path, caplog):
    """The flywheel treats a no-embeddings provider as an expected skip: no entry,
    no exception, and no per-turn WARNING noise (a quiet INFO instead)."""
    import logging

    from cadless.llm.providers.anthropic import AnthropicChatProvider

    async def go():
        s = _store(tmp_path)
        await s.init()
        p = await s.create_project("P")
        v = await s.add_version(p.id, "a plate", _CODE, ok=True)
        with caplog.at_level(logging.INFO, logger="cadless.distill"):
            out = await auto_distill(
                s,
                AnthropicChatProvider(config=base_settings),
                project_id=p.id,
                version_id=v.id,
                intent="a plate",
                code=_CODE,
                ok=True,
                metrics={"bbox": [40, 20, 5]},
            )
        assert out is None
        assert await s.list_kb_entries() == []
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]

    run(go())
