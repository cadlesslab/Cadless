"""Dynamic RAG retrieval into the codegen prompt.

At generation we embed the request the SAME way B3 embedded KB entries (NL intent
+ structural feature signature), retrieve top-k known-good entries via hybrid
retrieval (cosine similarity + feature-tag filter) behind a similarity floor, rank
them similarity-first / success-weighted, and inject them into the codegen prompt
as grounding ("suggestions to adapt, not templates to copy").

Purely additive: an empty KB (or all-below-floor) produces exactly today's prompt.

Offline only: deterministic fake ``embed`` + a temp store (like ``test_distill``).
"""

import asyncio
from pathlib import Path

from cadless.config import settings as base_settings
from cadless.distill import distill
from cadless.llm.providers.fake import FakeChatProvider
from cadless.prompts import build_user_message
from cadless.rag import (
    blended_score,
    format_grounding,
    retrieve_grounding,
    success_score,
)
from cadless.store import Store

_PLATE_CODE = """
length = 40
result = Box(length, 20, 5)
result = fillet(result.edges(), radius=2)
hole = Cylinder(3, 10)
result = result - hole
"""

_SPHERE_CODE = """
result = Sphere(12)
"""


def _store(tmp_path: Path) -> Store:
    return Store(db_path=tmp_path / "db.sqlite", artifacts_dir=tmp_path / "artifacts")


def _provider():
    cfg = base_settings.model_copy(update={"embed_dimensions": 64})
    return FakeChatProvider(config=cfg)


def run(coro):
    return asyncio.run(coro)


# ---- pure ranking helpers -------------------------------------------------


def test_success_score_blends_provenance_metrics():
    """More passed-assertions / kept / branched-from => a higher success score."""
    strong = success_score({"metrics": {"passed_assertions": 5, "kept": 3, "branched_from": 2}})
    weak = success_score({"metrics": {"passed_assertions": 0}})
    assert strong > weak
    # missing/empty provenance is well-defined (no crash) and not negative
    assert success_score({}) >= 0.0
    assert success_score({"metrics": {}}) >= 0.0


def test_blended_score_is_similarity_first():
    """Similarity dominates: a much higher similarity outranks a small success edge."""
    high_sim_low_success = blended_score(0.9, {"metrics": {}}, success_weight=0.2)
    low_sim_high_success = blended_score(
        0.4, {"metrics": {"passed_assertions": 99, "kept": 99}}, success_weight=0.2
    )
    assert high_sim_low_success > low_sim_high_success


def test_blended_score_breaks_ties_by_success():
    """At equal similarity, the higher-success entry ranks above the lower one."""
    more = blended_score(0.8, {"metrics": {"passed_assertions": 4}}, success_weight=0.2)
    less = blended_score(0.8, {"metrics": {"passed_assertions": 0}}, success_weight=0.2)
    assert more > less


# ---- grounding rendering --------------------------------------------------


def test_format_grounding_is_adapt_not_copy_and_bounded(tmp_path):
    """The grounding block is framed as adapt-not-copy and carries intent + code."""

    async def go():
        s = _store(tmp_path)
        await s.init()
        provider = _provider()
        p = await s.create_project("P")
        entry = await distill(
            s,
            provider,
            project_id=p.id,
            version_id=None,
            intent="a filleted plate with a hole",
            code=_PLATE_CODE,
            metrics={"bbox": [40, 20, 5]},
        )
        block = format_grounding([entry])
        assert "adapt" in block.lower()
        assert "not" in block.lower() and "templates to copy" in block.lower()
        assert "a filleted plate with a hole" in block
        assert "Box(length, 20, 5)" in block  # the code is included

    run(go())


def test_format_grounding_empty_when_no_entries():
    assert format_grounding([]) == ""


# ---- prompt injection (purely additive) -----------------------------------


def test_build_user_message_unchanged_without_grounding():
    """No grounding => byte-for-byte the legacy prompt."""
    legacy = build_user_message("a 5mm cube")
    assert build_user_message("a 5mm cube", grounding="") == legacy
    assert build_user_message("a 5mm cube", grounding=None) == legacy


def test_build_user_message_injects_grounding():
    """A grounding block is woven into the prompt, request preserved."""
    msg = build_user_message("a plate", grounding="GROUND-XYZ")
    assert "GROUND-XYZ" in msg
    assert "Request: a plate" in msg


# ---- end-to-end hybrid retrieval ------------------------------------------


def test_retrieve_grounding_empty_kb_returns_nothing(tmp_path):
    """Empty KB => no grounding (today's no-retrieval behavior)."""

    async def go():
        s = _store(tmp_path)
        await s.init()
        provider = _provider()
        block = await retrieve_grounding(
            s,
            provider,
            intent="a filleted plate with a hole",
            code=_PLATE_CODE,
            metrics={"bbox": [40, 20, 5]},
        )
        assert block == ""

    run(go())


def test_retrieve_grounding_returns_relevant_entry_above_floor(tmp_path):
    """A near-identical prior entry is retrieved and injected as grounding."""

    async def go():
        s = _store(tmp_path)
        await s.init()
        provider = _provider()
        p = await s.create_project("P")
        await distill(
            s,
            provider,
            project_id=p.id,
            version_id=None,
            intent="a filleted plate with a hole",
            code=_PLATE_CODE,
            metrics={"bbox": [40, 20, 5]},
        )
        # identical request => similarity 1.0, well above any sane floor
        block = await retrieve_grounding(
            s,
            provider,
            intent="a filleted plate with a hole",
            code=_PLATE_CODE,
            metrics={"bbox": [40, 20, 5]},
            floor=0.5,
        )
        assert block != ""
        assert "a filleted plate with a hole" in block
        assert "Box(length, 20, 5)" in block

    run(go())


def test_retrieve_grounding_below_floor_injects_nothing(tmp_path):
    """A weak query (best candidate below the floor) injects nothing, not noise."""

    async def go():
        s = _store(tmp_path)
        await s.init()
        provider = _provider()
        p = await s.create_project("P")
        await distill(
            s,
            provider,
            project_id=p.id,
            version_id=None,
            intent="a filleted plate with a hole",
            code=_PLATE_CODE,
            metrics={"bbox": [40, 20, 5]},
        )
        # an unrelated request: hash-based fake embeddings make it dissimilar.
        # With the floor at 0.99 nothing clears it.
        block = await retrieve_grounding(
            s,
            provider,
            intent="a totally different gear assembly",
            code=_SPHERE_CODE,
            metrics={"bbox": [24, 24, 24]},
            floor=0.99,
        )
        assert block == ""

    run(go())


def test_retrieve_grounding_tag_filter_excludes_wrong_tags(tmp_path):
    """An entry with no feature-tag overlap is excluded even if similar."""

    async def go():
        s = _store(tmp_path)
        await s.init()
        provider = _provider()
        p = await s.create_project("P")
        # Store a sphere entry whose tags (primitive:sphere, bbox:large) share
        # nothing with the plate query below (primitive:box/cylinder, op:*,
        # bbox:small).
        await distill(
            s,
            provider,
            project_id=p.id,
            version_id=None,
            intent="a filleted plate with a hole",
            code=_SPHERE_CODE,
            metrics={"bbox": [400, 400, 400]},
        )
        # Query with the SAME intent text (so cosine is high) but a plate
        # signature whose tags do not overlap the stored sphere entry. With the
        # tag filter required, the wrong-tag entry must be excluded.
        block = await retrieve_grounding(
            s,
            provider,
            intent="a filleted plate with a hole",
            code=_PLATE_CODE,
            metrics={"bbox": [40, 20, 5]},
            floor=0.0,
            require_tag_overlap=True,
        )
        assert block == ""

    run(go())


def test_blended_score_prefers_tag_overlap_at_equal_similarity():
    """In the default 'prefer overlap' mode, a tag-overlapping candidate outranks a
    non-overlapping one at otherwise-equal similarity/success."""
    overlap = blended_score(0.8, {"metrics": {}}, success_weight=0.2, tag_overlap=True)
    no_overlap = blended_score(0.8, {"metrics": {}}, success_weight=0.2, tag_overlap=False)
    assert overlap > no_overlap
    # but the preference is small: a materially higher similarity still wins
    assert blended_score(0.9, {"metrics": {}}, success_weight=0.2, tag_overlap=False) > overlap


def test_retrieve_grounding_respects_top_k(tmp_path):
    """At most top_k entries are injected."""

    async def go():
        s = _store(tmp_path)
        await s.init()
        provider = _provider()
        p = await s.create_project("P")
        for i in range(5):
            await distill(
                s,
                provider,
                project_id=p.id,
                version_id=None,
                intent=f"a filleted plate variant {i}",
                code=_PLATE_CODE,
                metrics={"bbox": [40, 20, 5]},
            )
        block = await retrieve_grounding(
            s,
            provider,
            intent="a filleted plate with a hole",
            code=_PLATE_CODE,
            metrics={"bbox": [40, 20, 5]},
            floor=0.0,
            top_k=2,
        )
        # exactly two entries rendered (count the per-entry markers)
        assert block.count("Example") == 2

    run(go())


def test_retrieve_grounding_skips_cleanly_when_provider_lacks_embeddings(tmp_path):
    """A provider with no embeddings API (anthropic) skips retrieval — the purely
    additive no-retrieval path — instead of crashing the turn."""

    async def go():
        s = _store(tmp_path)
        await s.init()
        seeder = _provider()
        p = await s.create_project("P")
        await distill(
            s,
            seeder,
            project_id=p.id,
            version_id=None,
            intent="a filleted plate with a hole",
            code=_PLATE_CODE,
            metrics={"bbox": [40, 20, 5]},
        )
        from cadless.llm.providers.anthropic import AnthropicChatProvider

        block = await retrieve_grounding(
            s,
            AnthropicChatProvider(config=base_settings),
            intent="a filleted plate with a hole",
            code=_PLATE_CODE,
            metrics={"bbox": [40, 20, 5]},
        )
        assert block == ""

    run(go())
