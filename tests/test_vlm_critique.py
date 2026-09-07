"""VLM critique tests. No network: a scripted provider and a fake renderer."""

from pathlib import Path

import pytest

from cadless.catalog.thumbnail import VIEW_ORDER
from cadless.config import Settings
from cadless.llm.provider import ImagesUnsupported
from cadless.llm.providers import StreamChunk
from cadless.llm.providers.fake import FakeChatProvider
from cadless.llm.types import Capabilities, StreamEvent
from cadless.pipeline import Pipeline
from cadless.vlm_critique import Critique, VlmCritic, parse_verdict

GOOD = "from build123d import *\nresult = Box(10, 10, 10)\n"


def _provider(reply: str) -> FakeChatProvider:
    return FakeChatProvider(script=[StreamChunk(StreamEvent.TEXT_DELTA, {"text": reply})])


class _BlindProvider(FakeChatProvider):
    """A provider whose model cannot see — the case that must be refused early."""

    def capabilities(self, model: str) -> Capabilities:
        return Capabilities(supports_images=False)


def _renderer(views_seen: list | None = None):
    """A renderer that records what it was asked for and returns marked bytes."""

    def render(mesh_path: str, views):
        if views_seen is not None:
            views_seen.append((mesh_path, tuple(views)))
        return [(name, f"PNG:{name}".encode()) for name in views]

    return render


def test_parse_verdict_match():
    assert parse_verdict("MATCH").matches
    assert parse_verdict("match, looks good").matches


def test_parse_verdict_mismatch_extracts_feedback():
    c = parse_verdict("MISMATCH: the hole is missing")
    assert not c.matches
    assert c.feedback == "the hole is missing"


def test_critic_sends_every_view_in_one_call_pictures_first():
    """Four views, one turn, images ahead of the words.

    Several calls would let the model answer about one view at a time, which is
    the opposite of what a multi-view critique is for; and the request ends on
    the question, so an image appended after it lands between the cue and the
    answer.
    """
    provider = _provider("MATCH")
    critic = VlmCritic(renderer=_renderer(), provider=provider)

    critic.critique("a cube", "/tmp/x.stl")

    assert len(provider.calls) == 1
    content = provider.calls[0]["messages"][0].content
    kinds = [block.kind for block in content]
    assert kinds == ["image"] * 4 + ["text"]
    assert all(block.media_type == "image/png" for block in content[:4])


def test_critic_asks_for_the_configured_number_of_views():
    seen: list = []
    cfg = Settings(vlm_critique_view_count=2)
    critic = VlmCritic(renderer=_renderer(seen), provider=_provider("MATCH"), config=cfg)

    critic.critique("a cube", "/tmp/x.stl")

    assert seen == [("/tmp/x.stl", VIEW_ORDER[:2])]


def test_critic_names_the_views_it_sent():
    """The verdict can only cite a view the request named."""
    provider = _provider("MATCH")
    VlmCritic(renderer=_renderer(), provider=provider).critique("a cube", "/tmp/x.stl")

    question = provider.calls[0]["messages"][0].content[-1].text
    for view in VIEW_ORDER[:4]:
        assert view in question
    assert "a cube" in question


def test_critic_sends_the_slug_the_adapter_resolves():
    """A pre-resolved vendor id is what every adapter is handed a slug to make.

    Resolving it here would raise on every call, and the pipeline treats a
    critique failure as a repair signal — so the rung would go quiet rather
    than loud.
    """
    provider = _provider("MATCH")
    cfg = Settings()
    VlmCritic(renderer=_renderer(), provider=provider, config=cfg).critique("a cube", "/x.stl")

    assert provider.calls[0]["model"] == cfg.vlm_model_slug
    assert provider.calls[0]["model"] != cfg.vlm_model_id


def test_critic_refuses_a_blind_model_before_rendering():
    """Refuse in the seam's own vocabulary rather than at the vendor."""
    rendered: list = []
    critic = VlmCritic(renderer=_renderer(rendered), provider=_BlindProvider())

    with pytest.raises(ImagesUnsupported):
        critic.critique("a cube", "/tmp/x.stl")
    assert rendered == [], "rendered before checking the model could see"


def test_critic_carries_the_captures_out():
    """The pipeline publishes what the reviewer saw, so it has to come back."""
    verdict = VlmCritic(renderer=_renderer(), provider=_provider("MISMATCH: too tall")).critique(
        "a cube", "/tmp/x.stl"
    )

    assert not verdict.matches
    assert verdict.feedback == "too tall"
    assert [name for name, _ in verdict.captures] == list(VIEW_ORDER[:4])
    assert verdict.captures[0][1] == b"PNG:front"


def test_no_vendor_sdk_reaches_past_the_seam():
    """The last vendor import outside a provider adapter lived in this module."""
    source = (Path(__file__).resolve().parents[1] / "cadless" / "vlm_critique.py").read_text()
    assert "boto3" not in source
    assert "bedrock" not in source.lower()


class AlwaysGood:
    def generate(
        self, intent, grounding=None, temperature=None, on_token=None, images=(), on_reading=None
    ):
        return GOOD

    def repair(self, intent, code, error, context=None, images=()):
        self.last_error = error
        return GOOD


class _ScriptedCritic:
    """Returns MISMATCH once, then MATCH — simulates a fixed-on-second-look part."""

    def __init__(self):
        self.verdicts = iter([Critique(False, "wrong size"), Critique(True, "")])
        self.paths: list[str] = []

    def critique(self, intent, mesh_path):
        self.paths.append(mesh_path)
        return next(self.verdicts)


@pytest.mark.build123d
def test_pipeline_off_by_default_skips_critique(tmp_path):
    gen = AlwaysGood()
    # critic present but flag default-off -> not consulted
    pipe = Pipeline(generator=gen, critic=_ScriptedCritic())
    result = pipe.run("a cube", export_dir=str(tmp_path))
    assert result.ok
    assert result.attempt_count == 1
    assert all(a.stage != "critique" for a in result.attempts)


@pytest.mark.build123d
def test_pipeline_critique_triggers_repair_when_enabled(tmp_path):
    gen = AlwaysGood()
    cfg = Settings(vlm_critique_enabled=True, repair_max_attempts=3)
    pipe = Pipeline(generator=gen, config=cfg, critic=_ScriptedCritic())
    result = pipe.run("a cube", export_dir=str(tmp_path))
    assert result.ok
    # first attempt executes-but-mismatches -> critique attempt, then success
    assert any(a.stage == "critique" for a in result.attempts)
    assert "wrong size" in gen.last_error
