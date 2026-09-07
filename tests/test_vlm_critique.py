"""VLM critique tests. No network: a scripted provider and a fake renderer."""

import base64
import json
from pathlib import Path

import pytest

from cadless.agent import _route_live_events
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
def test_pipeline_skips_critique_when_the_setting_is_off(tmp_path):
    """Switched off, a turn is what it was before any of this existed."""
    critic = _ScriptedCritic()
    cfg = Settings(vlm_critique_enabled=False)
    result = Pipeline(generator=AlwaysGood(), config=cfg, critic=critic).run(
        "a cube", export_dir=str(tmp_path)
    )
    assert result.ok
    assert result.attempt_count == 1
    assert all(a.stage != "critique" for a in result.attempts)
    assert critic.paths == [], "rendered and asked while switched off"


@pytest.mark.build123d
def test_a_pipeline_with_no_critic_never_critiques(tmp_path):
    """The setting is necessary, not sufficient — the critic has to be injected.

    This is what keeps the eval's baseline and the legacy generate route off
    the vision path now that the setting ships on: they build a bare pipeline.
    """
    events: list[dict] = []
    result = Pipeline(generator=AlwaysGood()).run(
        "a cube", export_dir=str(tmp_path), on_progress=events.append
    )
    assert result.ok
    assert all(a.stage != "critique" for a in result.attempts)
    assert not [e for e in events if e.get("event") == "critique"]


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


@pytest.mark.build123d
def test_pipeline_gates_the_critique_on_the_mesh_the_renderer_reads(tmp_path):
    """The gate and the call name the same artifact — see the judge's rung."""
    critic = _ScriptedCritic()
    cfg = Settings(vlm_critique_enabled=True, repair_max_attempts=3)
    Pipeline(generator=AlwaysGood(), config=cfg, critic=critic).run(
        "a cube", export_dir=str(tmp_path)
    )
    assert critic.paths and all(p.endswith(".stl") for p in critic.paths), critic.paths


class _CapturingCritic:
    """Returns pictures with its verdict, as the real critic does."""

    def __init__(self, verdicts):
        self._verdicts = iter(verdicts)

    def critique(self, intent, mesh_path):
        matches = next(self._verdicts)
        return Critique(
            matches=matches,
            feedback="" if matches else "wrong size",
            captures=[("front", b"PNG:front"), ("top", b"PNG:top")],
        )


class _BrokenCritic:
    """Stands in for a model that cannot see, or a provider that cannot be reached."""

    def __init__(self, exc):
        self._exc = exc
        self.calls = 0

    def critique(self, intent, mesh_path):
        self.calls += 1
        raise self._exc


@pytest.mark.build123d
@pytest.mark.parametrize(
    "exc",
    [ImagesUnsupported("fake"), RuntimeError("the provider is unreachable")],
    ids=["blind-model", "provider-down"],
)
def test_a_critique_that_cannot_be_taken_does_not_fail_the_build(tmp_path, exc):
    """It is an extra signal on a build that already succeeded.

    Letting it out turns every successful turn into a failed one wherever the
    configured model is not vision-capable — which, now that this runs by
    default, is the whole deployment rather than an unlucky turn.
    """
    events: list[dict] = []
    critic = _BrokenCritic(exc)
    cfg = Settings(vlm_critique_enabled=True, repair_max_attempts=3)

    result = Pipeline(generator=AlwaysGood(), config=cfg, critic=critic).run(
        "a cube", export_dir=str(tmp_path), on_progress=events.append
    )

    assert critic.calls == 1, "never reached the critic"
    assert result.ok, "an unavailable reviewer failed a build that succeeded"
    assert result.attempt_count == 1, "spent a repair attempt on an infrastructure failure"
    assert all(a.stage != "critique" for a in result.attempts)
    # Reported, not swallowed: a reviewer that never ran looks exactly like one
    # that always agreed, and that is the version nobody notices.
    said = [
        e
        for e in events
        if e.get("phase") == "critique" and "unavailable" in (e.get("error") or "")
    ]
    assert len(said) == 1, events


@pytest.mark.build123d
def test_pipeline_publishes_the_captures_on_every_round(tmp_path):
    """The reviewer reports as it goes, whichever way each verdict falls.

    Only publishing a mismatch would show the user the rounds that went wrong
    and hide the one that settled it, which is the round that answers "is it
    right now?".
    """
    events: list[dict] = []
    cfg = Settings(vlm_critique_enabled=True, repair_max_attempts=3)
    pipe = Pipeline(generator=AlwaysGood(), config=cfg, critic=_CapturingCritic([False, True]))

    result = pipe.run("a cube", export_dir=str(tmp_path), on_progress=events.append)

    assert result.ok
    shots = [e for e in events if e.get("event") == "critique"]
    assert len(shots) == 2
    assert [v["name"] for v in shots[0]["views"]] == ["front", "top"]
    assert base64.standard_b64decode(shots[0]["views"][0]["png_b64"]) == b"PNG:front"
    assert (shots[0]["matches"], shots[0]["feedback"]) == (False, "wrong size")
    assert shots[1]["matches"] is True
    # The SSE layer hands every event to json.dumps; bytes would break there.
    json.dumps(shots)


def test_critique_events_go_live_and_are_not_replayed():
    """The captures arrive while the loop runs, and exactly once.

    Collected progress events burst as one ``tool_progress`` after the tool
    settles — after the loop these belong to has finished — so a capture left
    in that stream arrives when it is no longer news. Forwarded live *and* left
    in the stream, it arrives twice.
    """
    live: list[dict] = []
    collected: list[dict] = []
    route = _route_live_events(collected.append, None, on_critique=live.append)

    route({"event": "critique", "attempt": 1, "views": []})
    route({"event": "stage", "phase": "build", "status": "ok", "attempt": 1})

    assert [e["event"] for e in live] == ["critique"]
    assert [e["event"] for e in collected] == ["stage"]


def test_the_chat_route_is_the_one_place_a_critic_is_injected():
    """Wiring, not configuration, is what makes the reviewer reachable.

    The setting was live for as long as this feature has existed and did
    nothing, because nothing ever passed a critic. Asserting the wiring here
    means the reverse mistake — shipping the setting on with no critic behind
    it — cannot pass either.
    """
    from backend.routers.chat import build_pipeline
    from cadless.catalog.thumbnail import render_views

    critic = build_pipeline()._critic

    assert isinstance(critic, VlmCritic)
    assert critic._render is render_views
    assert critic._provider is None, "built a provider before anything asked for one"
    assert Pipeline()._critic is None


def test_the_setting_ships_on():
    """The capability is not delivered while it is off for everyone."""
    assert Settings().vlm_critique_enabled is True


def test_critique_events_are_dropped_with_no_live_sink():
    """As with codegen: no sink, no payload in the burst.

    A consumer that cannot show a picture has no use for several hundred
    kilobytes of base64, and the verdict itself still reaches it on the stage
    event that carries the repair signal.
    """
    collected: list[dict] = []
    route = _route_live_events(collected.append, None)

    route({"event": "critique", "attempt": 1, "views": [{"name": "front", "png_b64": "x"}]})

    assert collected == []
