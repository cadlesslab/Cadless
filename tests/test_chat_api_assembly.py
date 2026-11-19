"""Assembly mode in chat: the check's findings, the opt-in gate, and what an edit inherits."""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

import backend.routers.chat as chat
from backend.app import create_app
from backend.assembly import assembly_findings
from cadless import printer_profile
from cadless.pipeline import GenerationResult
from cadless.store import Store
from tests.chat_api_helpers import (
    ForgePipeline,
    ScriptedProvider,
    StubPipeline,
    _clarify_turn,
    _events,
    _install,
    _messages,
    _stream_assembly,
    _stream_chat,
    _text_turn,
    _tool_turn,
)


@pytest.fixture
def store(tmp_path):
    return Store(db_path=tmp_path / "db.sqlite", artifacts_dir=tmp_path / "artifacts")


@pytest.fixture
def client(store):
    with TestClient(create_app(store=store)) as c:
        yield c


# --- the assembly check's findings, shown in the chat ---------------------

# What the check found on a build it refused.
_REFUSED = {
    "ok": False,
    "order": None,
    "attempt": 3,
    "measured": True,
    "failures": ["part 1 and part 2 share 40 mm^3 of interior volume; they must not overlap"],
    "unchecked": [
        "assembly order: part 2 and part 3 touch where the check cannot follow their "
        "surfaces exactly; a gap between them would let it rule"
    ],
    "joints": [],
    "releases": [],
}


def _findings_turn(monkeypatch, verdict):
    provider = ScriptedProvider(
        [
            _tool_turn(tool_use_id="tu-1", name="generate_model", tool_input={"spec": "a shelf"}),
            _text_turn("Done."),
        ]
    )
    refused = verdict is not None and verdict.get("ok") is False
    _install(monkeypatch, provider, pipeline=StubPipeline(ok=not refused, assembly=verdict))


def test_a_refused_assembly_streams_the_checks_findings_with_its_tool_result(
    client, store, monkeypatch
):
    # The chat shows why the parts were not accepted, in the check's own words,
    # failures apart from what it could not establish.
    _findings_turn(monkeypatch, _REFUSED)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    tresult = next(e for e in _stream_chat(client, pid) if e["event"] == "tool_result")

    assert tresult["assembly_findings"] == {
        "failures": _REFUSED["failures"],
        "unchecked": _REFUSED["unchecked"],
    }


def test_an_accepted_assembly_streams_no_findings(client, store, monkeypatch):
    accepted = {**_REFUSED, "ok": True, "failures": [], "unchecked": [], "order": [0, 1, 2]}
    _findings_turn(monkeypatch, accepted)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    tresult = next(e for e in _stream_chat(client, pid) if e["event"] == "tool_result")

    assert tresult["assembly_findings"] is None


@pytest.mark.parametrize(
    "verdict",
    [
        pytest.param(None, id="no assembly asked for"),
        # What the pipeline records when the build came out as one solid.
        pytest.param({"ok": None, "order": None, "attempt": 1, "measured": False}, id="one solid"),
    ],
)
def test_a_build_the_check_did_not_rule_on_streams_no_findings(client, store, monkeypatch, verdict):
    _findings_turn(monkeypatch, verdict)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    tresult = next(e for e in _stream_chat(client, pid) if e["event"] == "tool_result")

    assert tresult["assembly_findings"] is None


@pytest.mark.parametrize(
    "verdict",
    [
        {"ok": False, "failures": 5, "unchecked": None},
        {"ok": False, "failures": "part 1 and part 2 overlap", "unchecked": {}},
        {"ok": False, "failures": [None, 3, "  ", {"line": "x"}], "unchecked": [["y"]]},
    ],
    ids=["a number", "a bare string", "entries that are not words"],
)
def test_findings_of_an_unexpected_shape_are_not_shown(verdict):
    # A turn must not fail on the verdict's shape, nor show a reader characters
    # or objects as sentences.
    assert assembly_findings(verdict) is None


def test_only_the_findings_that_are_words_are_shown():
    verdict = {"ok": False, "failures": ["part 1 overlaps part 2", 7, " "], "unchecked": ["x"]}

    assert assembly_findings(verdict) == {
        "failures": ["part 1 overlaps part 2"],
        "unchecked": ["x"],
    }


def test_a_refused_assembly_keeps_its_findings_in_the_stored_tool_result(
    client, store, monkeypatch
):
    # A reloaded transcript reads the findings back from here, so the stored
    # tool result has to carry them in the same shape the check wrote them.
    _findings_turn(monkeypatch, _REFUSED)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]
    _stream_chat(client, pid)

    assistant = _messages(store, pid)[1]
    stored = next(b for b in assistant.blocks if b.kind == "tool_result")
    verdict = json.loads(stored.content)["assembly"]
    assert verdict["failures"] == _REFUSED["failures"]
    assert verdict["unchecked"] == _REFUSED["unchecked"]


def test_a_second_refusal_in_a_turn_keeps_its_findings_in_the_stored_tool_result(
    client, store, monkeypatch
):
    # From the second refusal at the same stage the agent adds a note steering
    # the model to ask; the stored result must still read back as the verdict.
    provider = ScriptedProvider(
        [
            _tool_turn(tool_use_id="tu-1", name="generate_model", tool_input={"spec": "one"}),
            _tool_turn(tool_use_id="tu-2", name="generate_model", tool_input={"spec": "two"}),
            _text_turn("I need a detail."),
        ]
    )
    _install(monkeypatch, provider, pipeline=StubPipeline(ok=False, assembly=_REFUSED))
    pid = client.post("/projects", json={"name": "P"}).json()["id"]
    _stream_chat(client, pid)

    assistant = _messages(store, pid)[1]
    stored = [json.loads(b.content) for b in assistant.blocks if b.kind == "tool_result"]
    assert [s["assembly"]["failures"] for s in stored] == [_REFUSED["failures"]] * 2
    assert "agent_note" in stored[1]


# --- assembly mode: opt-in toggle + both-true gate ------------------


def _assembly_turn(monkeypatch):
    provider = ScriptedProvider(
        [
            _tool_turn(tool_use_id="tu-1", name="generate_model", tool_input={"spec": "a shelf"}),
            _text_turn("Done."),
        ]
    )
    pipeline = StubPipeline()
    _install(monkeypatch, provider, pipeline=pipeline)
    return pipeline


def test_a_size_given_before_a_clarifying_question_holds_the_build_after_it(
    client, store, monkeypatch
):
    # Through the route and the loop both: the answer gives no size, and the
    # size the user gave a message earlier is still what the build is held to.
    monkeypatch.setattr(chat.settings, "assembly_enabled", True)
    provider = ScriptedProvider(
        [
            _clarify_turn(tool_use_id="tu-1", questions=[{"text": "PLA or PETG?"}]),
            _tool_turn(
                tool_use_id="tu-2",
                name="generate_model",
                tool_input={"spec": "a box", "requested_mm": [300, 200, 100]},
            ),
            _text_turn("Done."),
        ]
    )
    pipeline = StubPipeline()
    _install(monkeypatch, provider, pipeline=pipeline)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    _stream_assembly(client, pid, assembly=True, text="a box 300 x 200 x 100 mm, in parts")
    _stream_assembly(client, pid, assembly=True, text="PLA, please")

    assert pipeline.assemblies[-1].requested_mm == (300.0, 200.0, 100.0)


def _assembly_turns(monkeypatch, requested, *, before=1):
    """Turns through the route that each build once, the last stating ``requested``."""
    monkeypatch.setattr(chat.settings, "assembly_enabled", True)
    plain = [_tool_turn(tool_use_id="tu-1", name="generate_model", tool_input={"spec": "a"})]
    provider = ScriptedProvider(
        [
            *[turn for _ in range(before) for turn in (*plain, _text_turn("Done."))],
            _tool_turn(
                tool_use_id="tu-2",
                name="generate_model",
                tool_input={"spec": "b", "requested_mm": requested},
            ),
            _text_turn("Done."),
        ]
    )
    return provider


def test_a_size_given_for_the_model_already_built_backs_no_side_of_the_next(
    client, store, monkeypatch
):
    # The wheels were 70 mm on the model the first turn kept; a truck asked for
    # after it is not held to a 70 mm side the orchestrator happened to choose.
    provider = _assembly_turns(monkeypatch, [500, 200, 70])
    pipeline = StubPipeline()
    _install(monkeypatch, provider, pipeline=pipeline)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    _stream_assembly(client, pid, assembly=True, text="make the wheels 70 mm")
    _stream_assembly(client, pid, assembly=True, text="now an RC truck, 500 mm long")

    assert pipeline.assemblies[-1].requested_mm == (500.0,)


def test_a_size_given_for_a_copied_model_backs_no_side_in_the_copy(client, store, monkeypatch):
    # A copy is given its versions before its transcript, so by the clock every
    # copied message comes after the model it asked for.
    provider = _assembly_turns(monkeypatch, [500, 200, 70])
    pipeline = StubPipeline()
    _install(monkeypatch, provider, pipeline=pipeline)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]
    _stream_assembly(client, pid, assembly=True, text="make the wheels 70 mm")
    copy = client.post(f"/projects/{pid}/clone", json={"name": "P (copy)"}).json()["id"]

    _stream_assembly(client, copy, assembly=True, text="now an RC truck, 500 mm long")

    assert pipeline.assemblies[-1].requested_mm == (500.0,)


def _seeded(client, store):
    """A project whose current model no chat turn built, as the parameter panel makes one."""
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    async def seed():
        version = await store.add_version(
            pid, "base", "result = 1\n", ok=True, volume=1.0, bbox=(84.0, 66.0, 36.05)
        )
        await store.set_current_version(pid, version.id)

    asyncio.run(seed())
    return pid


def test_a_size_given_before_a_question_about_a_panel_made_model_still_holds(
    client, store, monkeypatch
):
    # Every settled turn names the current version, the question too; the
    # message before it came after that model, and is still the request.
    monkeypatch.setattr(chat.settings, "assembly_enabled", True)
    provider = ScriptedProvider(
        [
            _clarify_turn(tool_use_id="tu-1", questions=[{"text": "PLA or PETG?"}]),
            _tool_turn(
                tool_use_id="tu-2",
                name="edit_model",
                tool_input={"change": "a box", "requested_mm": [300, 200, 100]},
            ),
            _text_turn("Done."),
        ]
    )
    pipeline = StubPipeline()
    _install(monkeypatch, provider, pipeline=pipeline)
    pid = _seeded(client, store)

    _stream_assembly(client, pid, assembly=True, text="make it a box 300 x 200 x 100 mm")
    _stream_assembly(client, pid, assembly=True, text="PLA, please")

    assert pipeline.assemblies[-1].requested_mm == (300.0, 200.0, 100.0)


def test_a_retry_after_a_failed_build_on_a_panel_made_model_still_holds(client, store, monkeypatch):
    # The failed turn is linked to the model it fell back to, the panel's.
    provider = _assembly_turns(monkeypatch, [400])
    pipeline = StubPipeline(ok=False, error="the build failed")
    _install(monkeypatch, provider, pipeline=pipeline)
    pid = _seeded(client, store)

    _stream_assembly(client, pid, assembly=True, text="an RC car, 400 mm long")
    _stream_assembly(client, pid, assembly=True, text="try again")

    assert pipeline.assemblies[-1].requested_mm == (400.0,)


def test_retries_of_turns_that_kept_nothing_back_each_side_once(client, store, monkeypatch):
    # The first turns kept no model, so their words are still the request; sent
    # again and again by Retry, "400 mm long" is still one side.
    provider = _assembly_turns(monkeypatch, [400, 400, 70], before=2)
    pipeline = StubPipeline(ok=False, error="the build failed")
    _install(monkeypatch, provider, pipeline=pipeline)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    for _ in range(3):
        _stream_assembly(client, pid, assembly=True, text="an RC car, 400 mm long")

    assert pipeline.assemblies[-1].requested_mm == (400.0,)


def test_a_figure_only_the_assistant_wrote_backs_no_side(client, store, monkeypatch):
    # The replayed history carries the assistant's words, and a figure there is
    # the orchestrator's own suggestion, not the user's request.
    monkeypatch.setattr(chat.settings, "assembly_enabled", True)
    provider = ScriptedProvider(
        [
            _text_turn("A width of 250 mm would suit it."),
            _tool_turn(
                tool_use_id="tu-1",
                name="generate_model",
                tool_input={"spec": "a shelf", "requested_mm": [250]},
            ),
            _text_turn("Done."),
        ]
    )
    pipeline = StubPipeline()
    _install(monkeypatch, provider, pipeline=pipeline)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    _stream_assembly(client, pid, assembly=True, text="what width would suit a shelf?")
    _stream_assembly(client, pid, assembly=True, text="go ahead and make it")

    assert pipeline.assemblies[-1].requested_mm is None


def test_assembly_opt_in_with_switch_on_reaches_the_pipeline(client, store, monkeypatch):
    """Both gates true: the turn carries a spec sized to the printer that is saved."""
    monkeypatch.setattr(chat.settings, "assembly_enabled", True)
    pipeline = _assembly_turn(monkeypatch)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    _stream_assembly(client, pid, assembly=True)

    spec = pipeline.assemblies[0]
    assert spec is not None
    assert (spec.volume.width, spec.volume.depth, spec.volume.height) == (
        printer_profile.DEFAULT_BED_WIDTH,
        printer_profile.DEFAULT_BED_DEPTH,
        printer_profile.DEFAULT_MAX_HEIGHT,
    )
    assert spec.clearance_mm == printer_profile.DEFAULT_JOINT_CLEARANCE


def test_assembly_spec_carries_the_saved_clearance(client, store, monkeypatch):
    """A printer needing a wider gap gets one. This is the whole reason the
    clearance is saved state rather than a constant written into the prompt."""
    monkeypatch.setattr(chat.settings, "assembly_enabled", True)
    monkeypatch.setattr(chat.user_settings, "load", lambda: {"printer_joint_clearance": 0.35})
    pipeline = _assembly_turn(monkeypatch)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    _stream_assembly(client, pid, assembly=True)

    assert pipeline.assemblies[0].clearance_mm == 0.35


def test_assembly_opt_in_but_switch_off_sends_nothing(client, store, monkeypatch):
    """Per-turn opt-in WITHOUT the global switch => today's single-solid prompt."""
    monkeypatch.setattr(chat.settings, "assembly_enabled", False)
    pipeline = _assembly_turn(monkeypatch)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    _stream_assembly(client, pid, assembly=True)

    assert pipeline.assemblies == [None]


def test_a_build_asked_for_as_an_assembly_while_the_switch_is_off_is_not_one(
    client, store, monkeypatch
):
    """Built without the spec it is one solid. Remembered as an assembly, the
    first edit after the switch came on would be held to a printer bed and asked
    to split a model nobody split."""
    monkeypatch.setattr(chat.settings, "assembly_enabled", False)
    pipeline = _build_then_edit(monkeypatch)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    _stream_assembly(client, pid, assembly=True)
    monkeypatch.setattr(chat.settings, "assembly_enabled", True)
    _stream_assembly(client, pid, assembly=False, text="make it taller")

    assert pipeline.assemblies == [None, None]


def test_forge_losers_asked_for_as_an_assembly_while_the_switch_is_off_are_not_one(
    client, store, monkeypatch
):
    """The race's losers are kept as candidates to switch to, and follow the
    winner's rule: an assembly only when the turn was built as one."""
    monkeypatch.setattr(chat.settings, "assembly_enabled", False)
    monkeypatch.setattr(chat.settings, "forge_enabled", True)
    monkeypatch.setattr(chat.settings, "forge_budget", 6)
    monkeypatch.setattr(chat.settings, "forge_candidate_cost", 2)
    monkeypatch.setattr(chat.settings, "forge_min_n", 2)
    monkeypatch.setattr(chat.settings, "forge_max_n", 5)
    provider = ScriptedProvider(
        [
            _tool_turn(tool_use_id="tu-1", name="generate_model", tool_input={"spec": "a cube"}),
            _text_turn("Done."),
        ]
    )
    _install(monkeypatch, provider, pipeline=ForgePipeline())
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    body = {"message": "make a cube", "forge": True, "assembly": True}
    with client.stream("POST", f"/projects/{pid}/chat", json=body) as r:
        events = _events("".join(r.iter_text()))
    winner_id = next(e for e in events if e["event"] == "tool_result")["version_id"]

    candidates = asyncio.run(store.list_candidate_versions(winner_id))
    assert candidates
    assert [c.built_as_assembly for c in candidates] == [False] * len(candidates)


def test_assembly_switch_on_but_opt_out_sends_nothing(client, store, monkeypatch):
    """The switch alone is necessary and not sufficient — the turn must opt in."""
    monkeypatch.setattr(chat.settings, "assembly_enabled", True)
    pipeline = _assembly_turn(monkeypatch)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    _stream_assembly(client, pid, assembly=False)

    assert pipeline.assemblies == [None]


def test_assembly_defaults_off_when_flag_omitted(client, store, monkeypatch):
    """Omitting the flag entirely behaves as opt-out even with the switch on."""
    monkeypatch.setattr(chat.settings, "assembly_enabled", True)
    pipeline = _assembly_turn(monkeypatch)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    # No "assembly" key in the body at all.
    _stream_chat(client, pid, "make a bookshelf")

    assert pipeline.assemblies == [None]


def test_steer_accepts_the_assembly_flag(client, store, monkeypatch):
    """``SteerRequest`` is ``extra="forbid"``, so a flag the composer sends on a
    chat body would 422 here unless it is declared. Same trap ``forge`` hit."""
    _install(monkeypatch, ScriptedProvider([_text_turn("ok")]))
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    r = client.post(
        f"/projects/{pid}/chat/steer", json={"message": "make it taller", "assembly": True}
    )

    assert r.status_code == 202


def _build_then_edit(monkeypatch, edits: int = 1):
    """One fresh build followed by ``edits`` edits of what it made, one provider."""
    turns = [
        _tool_turn(tool_use_id="tu-1", name="generate_model", tool_input={"spec": "a shelf"}),
        _text_turn("Done."),
    ]
    for n in range(edits):
        turns += [
            _tool_turn(
                tool_use_id=f"tu-e{n}", name="edit_model", tool_input={"change": "20 mm taller"}
            ),
            _text_turn("Edited."),
        ]
    pipeline = StubPipeline()
    _install(monkeypatch, ScriptedProvider(turns), pipeline=pipeline)
    return pipeline


def test_an_edit_keeps_the_assembly_the_version_was_built_with(client, store, monkeypatch):
    """The second turn is the one a reload produces: the option has gone back to
    off, and the person is editing a model that is in pieces.

    Without this, the edit is not merely unguided: it would lose the bed and the
    clearance the model was split for, and its parts would be held only to the
    check for a build nobody asked to split.
    """
    monkeypatch.setattr(chat.settings, "assembly_enabled", True)
    pipeline = _build_then_edit(monkeypatch)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    _stream_assembly(client, pid, assembly=True)
    # A different printer saved between the two turns. Asserting the edit picks it
    # up is what pins the central decision: the version remembers that it is an
    # assembly, never which machine it was built for. Comparing the two specs to
    # each other instead would pass equally well if the spec were stored and
    # replayed, which is the design this rejected.
    monkeypatch.setattr(chat.user_settings, "load", lambda: {"printer_joint_clearance": 0.35})
    _stream_assembly(client, pid, assembly=False, text="make it 20 mm taller")

    built, edited = pipeline.assemblies
    assert built is not None
    assert edited is not None
    assert built.clearance_mm == printer_profile.DEFAULT_JOINT_CLEARANCE
    assert edited.clearance_mm == 0.35


class _SecondFreshBuildFails(StubPipeline):
    """The second fresh build fails and leaves its code behind, as the repair loop
    does when its budget runs out -- code the agent then edits."""

    def __init__(self) -> None:
        super().__init__()
        self.fresh = 0

    def run(self, intent, export_dir=None, on_progress=None, prior_code=None, **kwargs):
        if prior_code is None:
            self.fresh += 1
            if self.fresh == 2:
                self.assemblies.append(kwargs.get("assembly"))
                return GenerationResult(
                    ok=False,
                    intent=intent,
                    code='params = {"size": 10}\nresult = None\n',
                    error="execute: boom",
                )
        return super().run(intent, export_dir, on_progress, prior_code, **kwargs)


@pytest.mark.parametrize(
    "pipeline", [StubPipeline, _SecondFreshBuildFails], ids=["it built", "it failed to build"]
)
def test_an_edit_after_a_fresh_build_in_the_same_turn_follows_the_fresh_model(
    client, store, monkeypatch, pipeline
):
    """A turn that starts on an assembly, builds a new model and then edits it.

    The edit acts on the new model, which is an assembly only if this turn asked:
    held to the starting model's answer it would be judged against a printer bed
    and recorded as split, as if it were the model it replaced.
    """
    monkeypatch.setattr(chat.settings, "assembly_enabled", True)
    turns = [
        _tool_turn(tool_use_id="tu-1", name="generate_model", tool_input={"spec": "a shelf"}),
        _text_turn("Done."),
        _tool_turn(tool_use_id="tu-2", name="generate_model", tool_input={"spec": "a cube"}),
        _tool_turn(tool_use_id="tu-3", name="edit_model", tool_input={"change": "taller"}),
        _text_turn("Edited."),
    ]
    stub = pipeline()
    _install(monkeypatch, ScriptedProvider(turns), pipeline=stub)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    _stream_assembly(client, pid, assembly=True)
    _stream_assembly(client, pid, assembly=False, text="forget that, a simple cube, then taller")

    assert stub.assemblies[0] is not None
    assert stub.assemblies[1:] == [None, None]
    current = client.get(f"/projects/{pid}").json()["current_version_id"]
    assert asyncio.run(store.get_version(current)).built_as_assembly is False


def test_an_edit_to_a_model_never_built_as_an_assembly_still_sends_nothing(
    client, store, monkeypatch
):
    """The other half, and the one that keeps the fix honest. Remembering must be
    remembering, not a second way to opt in: a project whose model was never an
    assembly has to reach the pipeline with nothing, or every edit in the product
    would start being measured against a printer bed it never asked about.
    """
    monkeypatch.setattr(chat.settings, "assembly_enabled", True)
    pipeline = _build_then_edit(monkeypatch)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    _stream_assembly(client, pid, assembly=False)
    _stream_assembly(client, pid, assembly=False, text="make it 20 mm taller")

    assert pipeline.assemblies == [None, None]


def test_a_fresh_build_in_an_assembly_project_is_not_told_to_split(client, store, monkeypatch):
    """Remembering is about the model, not about the project.

    "Forget that, make me a simple cube" starts a new model, and a new model is an
    assembly only if this turn says so. Inheriting here would make the opt-in
    permanent — switching the composer's toggle off for the turn would take
    nothing back — and the cube would then be recorded as an assembly itself, so
    every later turn in the project would carry a brief that opens by saying the
    part is too large to print in one piece.
    """
    monkeypatch.setattr(chat.settings, "assembly_enabled", True)
    provider = ScriptedProvider(
        [
            _tool_turn(tool_use_id="tu-1", name="generate_model", tool_input={"spec": "a shelf"}),
            _text_turn("Done."),
            _tool_turn(tool_use_id="tu-2", name="generate_model", tool_input={"spec": "a cube"}),
            _text_turn("Done."),
        ]
    )
    pipeline = StubPipeline()
    _install(monkeypatch, provider, pipeline=pipeline)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    _stream_assembly(client, pid, assembly=True)
    _stream_assembly(client, pid, assembly=False, text="forget that, a simple cube")

    built, fresh = pipeline.assemblies
    assert built is not None
    assert fresh is None


def test_the_kill_switch_stops_a_remembered_assembly_without_forgetting_it(
    client, store, monkeypatch
):
    """Two properties of the switch, and they pull in opposite directions.

    It has to be outermost, or a version's record would keep the feature alive
    after it was turned off — which is the one thing a kill-switch must not allow.
    But it must not be destructive either: recording "this turn had no spec" while
    it is off would erase the memory of a model that is still in pieces, and
    turning the switch back on would not bring it back. So the middle turn gets
    nothing and the third one still does.
    """
    monkeypatch.setattr(chat.settings, "assembly_enabled", True)
    pipeline = _build_then_edit(monkeypatch, edits=2)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    _stream_assembly(client, pid, assembly=True)
    monkeypatch.setattr(chat.settings, "assembly_enabled", False)
    _stream_assembly(client, pid, assembly=False, text="make it taller")
    monkeypatch.setattr(chat.settings, "assembly_enabled", True)
    _stream_assembly(client, pid, assembly=False, text="make it wider")

    built, while_off, back_on = pipeline.assemblies
    assert built is not None
    assert while_off is None
    assert back_on is not None
