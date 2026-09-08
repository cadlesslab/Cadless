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
from cadless.llm.types import Capabilities, StopReason, StreamEvent
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
    assert parse_verdict("match").matches
    assert parse_verdict("**MATCH**").matches
    assert parse_verdict("MATCH.").matches


@pytest.mark.parametrize(
    "reply",
    [
        "The renders show a plate.\nMatches: outer diameter.\nDoes not match: the counterbore is gone",
        "Matching this against the request, the profile is wrong and",
        "Matched features: the outer profile only; the hole is",
        "match, looks good",
    ],
    ids=["enumeration", "truncated-participle", "truncated-past", "trailing-prose"],
)
def test_a_line_that_merely_contains_the_word_is_not_a_pass(reply):
    """A pass is the dangerous direction, so the line has to be the verdict.

    Each of these was produced by, or is one edit away from, what a real vision
    model writes. The first literally says the part is wrong on its last line;
    read by prefix it came back as a match, which suppresses the repair and
    ships the wrong shape marked reviewed. A verdict missed instead costs one
    skipped review, and the caller already handles that.
    """
    with pytest.raises(ValueError):
        parse_verdict(reply)


def test_parse_verdict_mismatch_extracts_feedback():
    c = parse_verdict("MISMATCH: the hole is missing")
    assert not c.matches
    assert c.feedback == "the hole is missing"


def test_the_verdict_is_read_from_the_end_of_a_reasoned_reply():
    """The shape a real vision model actually answers in.

    Asked for "exactly MATCH" it still walks the views first and reaches the
    word several sentences in. Read only from the front, that reply is either
    unparseable or — worse, before this was measured — counted as a mismatch,
    which discards a part that was correct.
    """
    reasoned = (
        "Looking at the renders:\n\n"
        "- The top view shows a hexagonal outline with a circular hole ✓\n"
        "- The front and right views show a prism of even thickness ✓\n\n"
        "MATCH"
    )
    assert parse_verdict(reasoned).matches

    disagreeing = (
        "The top view shows a square, not a hexagon, and I see no hole.\n\n"
        "MISMATCH: the profile is square and the through-hole is missing"
    )
    verdict = parse_verdict(disagreeing)
    assert not verdict.matches
    assert verdict.feedback == "the profile is square and the through-hole is missing"


def test_mismatch_wins_over_the_word_it_contains():
    """`MISMATCH` starts with `MATCH`, so the order the lines are tested matters."""
    assert not parse_verdict("MISMATCH: too tall").matches


@pytest.mark.parametrize(
    "reply",
    [
        "MISMATCH: the hole is missing",
        "MISMATCH - the hole is missing",
        "MISMATCH — the hole is missing",
        "MISMATCH, the hole is missing",
        "**MISMATCH**: the hole is missing",
        "**MISMATCH** - the hole is missing",
        "- MISMATCH: the hole is missing",
        "> MISMATCH: the hole is missing",
        "`MISMATCH: the hole is missing`",
    ],
    ids=[
        "colon",
        "dash",
        "em-dash",
        "comma",
        "bold-colon",
        "bold-dash",
        "bullet",
        "blockquote",
        "code-span",
    ],
)
def test_a_mismatch_keeps_its_reason_however_it_is_written(reply):
    """The reason is what the reasoning was paid for; a separator must not lose it.

    It becomes the repair round's whole signal and the sentence the person
    reading the transcript sees, so a mismatch that arrives with a dash instead
    of a colon — or inside a bullet, a quote or a code span — must not come back
    as the generic wording.
    """
    verdict = parse_verdict(reply)
    assert not verdict.matches
    assert verdict.feedback == "the hole is missing"


def test_the_token_alone_is_not_a_description_of_the_defect():
    assert parse_verdict("MISMATCH").feedback == "model does not match the request"


def test_a_reason_that_opens_with_a_minus_sign_keeps_it():
    """Only punctuation that joined the token to the reason is removed.

    A dimension is a perfectly ordinary way to start a finding, and stripping
    leading punctuation blindly would turn "-3 mm too short" into "3 mm too
    short" — the opposite defect, reported confidently.
    """
    assert parse_verdict("MISMATCH: -3 mm too short").feedback == "-3 mm too short"


def test_a_second_terminal_event_cannot_clear_the_truncation():
    """Adapters emit a terminal event per delta, not once per turn.

    Assigned rather than accumulated, a later event carrying an ordinary stop
    reason wipes the flag the truncation set, and the fragment goes back to the
    parser — which is the door this guard was added to close.
    """
    provider = FakeChatProvider(
        script=[
            StreamChunk(StreamEvent.TEXT_DELTA, {"text": "Looking at the renders: the top"}),
            StreamChunk(StreamEvent.TURN_DELTA, {"stop_reason": StopReason.MAX_TOKENS}),
            StreamChunk(StreamEvent.TURN_DELTA, {"stop_reason": StopReason.END_TURN}),
        ]
    )

    with pytest.raises(ValueError, match="token ceiling"):
        VlmCritic(renderer=_renderer(), provider=provider).critique("a cube", "/tmp/x.stl")


def test_a_reply_cut_off_at_the_ceiling_is_refused():
    """Truncation is closed where it happens, not guessed at from the fragment.

    A reply the ceiling ended has no verdict at its end, and the tail it does
    have is whatever sentence the cut landed in — which is exactly the material
    a prefix read turns into a false pass.
    """
    provider = FakeChatProvider(
        script=[
            StreamChunk(StreamEvent.TEXT_DELTA, {"text": "Looking at the renders: the top view"}),
            StreamChunk(StreamEvent.TURN_DELTA, {"stop_reason": StopReason.MAX_TOKENS}),
        ]
    )
    critic = VlmCritic(renderer=_renderer(), provider=provider)

    with pytest.raises(ValueError, match="token ceiling"):
        critic.critique("a cube", "/tmp/x.stl")


@pytest.mark.parametrize(
    "reply",
    ["", "   ", "Sure! That looks like a good bracket to me.", "I can't see the image."],
    ids=["empty", "blank", "chatty", "refusal"],
)
def test_an_unreadable_verdict_is_refused_rather_than_read_as_a_mismatch(reply):
    """A mismatch discards code that built successfully; a parse failure must not.

    An empty stream, a reply cut off at the token ceiling, or a preamble that
    never reaches the word would otherwise throw away a working part and spend
    a repair round regenerating it, on the strength of a sentence nobody read.
    """
    with pytest.raises(ValueError):
        parse_verdict(reply)


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
def test_the_orchestrator_is_told_the_verdict_and_never_the_words(tmp_path):
    """A boolean and an attempt number reach the model. The prose never does.

    The transcript goes to real trouble to keep a vision model's free text —
    written from a prompt carrying the user's own — out of what the orchestrator
    is told. The tool payload is a second door into the same place, and until
    this assertion existed the only thing holding it shut was the shape of a
    dict two files away.
    """
    from cadless.agent import _result_summary

    cfg = Settings(vlm_critique_enabled=True, repair_max_attempts=1)
    result = Pipeline(generator=AlwaysGood(), config=cfg, critic=_CapturingCritic([False])).run(
        "a cube", export_dir=str(tmp_path)
    )

    payload = _result_summary(result)
    assert payload["critique"] == {"matches": False, "attempt": 1}
    assert set(payload["critique"]) == {"matches", "attempt"}, "prose reached the orchestrator"


@pytest.mark.build123d
def test_a_build_nobody_reviewed_reports_no_verdict(tmp_path):
    """A verdict belongs to the build it looked at, not to the turn.

    Carried across attempts, an earlier round's pass ends up attached to a later
    build the reviewer never saw — the reviewer that never ran looking exactly
    like the one that always agreed.
    """

    class _OnceThenBroken:
        def __init__(self):
            self.calls = 0

        def critique(self, intent, mesh_path):
            self.calls += 1
            if self.calls == 1:
                return Critique(matches=False, feedback="too tall", captures=[])
            raise RuntimeError("the provider went away")

    cfg = Settings(vlm_critique_enabled=True, repair_max_attempts=2)
    result = Pipeline(generator=AlwaysGood(), config=cfg, critic=_OnceThenBroken()).run(
        "a cube", export_dir=str(tmp_path)
    )

    assert result.ok
    assert result.critique is None, "a verdict from an earlier build followed the delivered one"


@pytest.mark.build123d
def test_the_last_attempt_is_reviewed_too(tmp_path):
    """Skip it and the build actually delivered is the one nobody looked at.

    It also makes "ran out of budget" indistinguishable from "was never
    checked", and leaves the newest verdict in the transcript describing a
    build that was afterwards replaced. With no budget left the finding is
    reported and the part is handed over with it attached.
    """
    events: list[dict] = []
    cfg = Settings(vlm_critique_enabled=True, repair_max_attempts=2)
    pipe = Pipeline(generator=AlwaysGood(), config=cfg, critic=_CapturingCritic([False, False]))

    result = pipe.run("a cube", export_dir=str(tmp_path), on_progress=events.append)

    rounds = [e for e in events if e.get("event") == "critique"]
    assert [r["attempt"] for r in rounds] == [1, 2], "the delivered attempt went unreviewed"
    assert result.ok, "a finding with no budget left must still deliver the build"
    assert rounds[-1]["matches"] is False, "the transcript must say the budget ended it"


@pytest.mark.build123d
def test_a_forge_candidate_does_not_critique(tmp_path):
    """N candidates would pay N times over for a signal the judge derives once.

    The fan-out also runs with no progress sink, so every capture a candidate
    produced is dropped before anyone could have seen it.
    """
    critic = _ScriptedCritic()
    cfg = Settings(vlm_critique_enabled=True, forge_candidate_count=2)

    results = Pipeline(generator=AlwaysGood(), config=cfg, critic=critic).run_candidates(
        "a cube", n=2, export_dir=str(tmp_path)
    )

    assert len(results) == 2 and all(r.ok for r in results)
    assert critic.paths == [], "a candidate paid for a critique nobody can see"


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
    # The default is what every other construction site gets, so this one
    # assertion is what keeps a pipeline nobody is watching from paying for
    # vision — the eval's baseline and the legacy generate route both build
    # their pipeline with no arguments.
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
