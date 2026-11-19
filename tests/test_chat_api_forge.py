"""Forge mode's gate, Blueprint rollback, and a regeneration keeping the assembly it replaces."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

import backend.routers.chat as chat
from backend.app import create_app
from cadless.pipeline import GenerationResult
from cadless.store import Store
from tests.chat_api_helpers import (
    ForgePipeline,
    ScriptedProvider,
    SequencedPipeline,
    StubPipeline,
    _events,
    _install,
    _messages,
    _plan_turn,
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


# --- forge mode: opt-in toggle + both-true gate (C4) ---------------


def _versions(store, pid):
    import asyncio

    return asyncio.run(store.list_versions(pid))


def _stream_forge(client, pid, *, forge: bool, text="make a cube"):
    body = {"message": text, "forge": forge}
    with client.stream("POST", f"/projects/{pid}/chat", json=body) as r:
        assert r.status_code == 200
        return _events("".join(r.iter_text()))


def test_forge_opt_in_with_switch_on_races_and_persists_winner_plus_losers(
    client, store, monkeypatch
):
    """Both gates true: a fresh generate races N, persists the winner as current and
    the losers as non-current candidate rows."""
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
    pipeline = ForgePipeline()
    _install(monkeypatch, provider, pipeline=pipeline)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    events = _stream_forge(client, pid, forge=True)
    tresult = next(e for e in events if e["event"] == "tool_result")
    assert tresult["ok"] is True

    # The race ran (N = 6//2 = 3); the single-run path was NOT used.
    assert pipeline.candidate_ns == [3]
    assert pipeline.run_count == 0

    # Winner is the project's current version.
    winner_id = tresult["version_id"]
    assert client.get(f"/projects/{pid}").json()["current_version_id"] == winner_id

    # Losers persisted as non-current candidate rows of the winner.
    candidates = asyncio.run(store.list_candidate_versions(winner_id))
    assert len(candidates) == 2
    for c in candidates:
        assert c.candidate_of_version_id == winner_id
        assert c.id != winner_id


def test_forge_opt_in_but_switch_off_does_not_race(client, store, monkeypatch):
    """Per-turn opt-in WITHOUT the global switch => normal single-run path."""
    monkeypatch.setattr(chat.settings, "forge_enabled", False)
    provider = ScriptedProvider(
        [
            _tool_turn(tool_use_id="tu-1", name="generate_model", tool_input={"spec": "a cube"}),
            _text_turn("Done."),
        ]
    )
    pipeline = ForgePipeline()
    _install(monkeypatch, provider, pipeline=pipeline)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    events = _stream_forge(client, pid, forge=True)
    tresult = next(e for e in events if e["event"] == "tool_result")
    assert tresult["ok"] is True

    assert pipeline.candidate_ns == []  # never raced
    assert pipeline.run_count == 1
    # Exactly one version persisted, no candidate rows — today's behavior.
    assert len(_versions(store, pid)) == 1
    assert asyncio.run(store.list_candidate_versions(tresult["version_id"])) == []


def test_forge_switch_on_but_opt_out_does_not_race(client, store, monkeypatch):
    """Global switch on but the turn did NOT opt in => normal single-run path."""
    monkeypatch.setattr(chat.settings, "forge_enabled", True)
    provider = ScriptedProvider(
        [
            _tool_turn(tool_use_id="tu-1", name="generate_model", tool_input={"spec": "a cube"}),
            _text_turn("Done."),
        ]
    )
    pipeline = ForgePipeline()
    _install(monkeypatch, provider, pipeline=pipeline)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    events = _stream_forge(client, pid, forge=False)
    tresult = next(e for e in events if e["event"] == "tool_result")
    assert tresult["ok"] is True

    assert pipeline.candidate_ns == []
    assert pipeline.run_count == 1
    assert len(_versions(store, pid)) == 1


def test_forge_defaults_off_when_flag_omitted(client, store, monkeypatch):
    """Omitting the forge flag entirely behaves as opt-out even with the switch on."""
    monkeypatch.setattr(chat.settings, "forge_enabled", True)
    provider = ScriptedProvider(
        [
            _tool_turn(tool_use_id="tu-1", name="generate_model", tool_input={"spec": "a cube"}),
            _text_turn("Done."),
        ]
    )
    pipeline = ForgePipeline()
    _install(monkeypatch, provider, pipeline=pipeline)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    # No "forge" key in the body at all.
    events = _stream_chat(client, pid, "make a cube")
    tresult = next(e for e in events if e["event"] == "tool_result")
    assert tresult["ok"] is True
    assert pipeline.candidate_ns == []
    assert pipeline.run_count == 1


# --- Blueprint rollback policy + replan (D3) -----------------------


def test_planned_turn_failed_step_reverts_to_last_ok_and_orchestrator_continues(
    client, store, monkeypatch
):
    """D3: in a planned (Blueprint) turn, a step that succeeds then a step that
    fails must auto-revert the project's current version to the last OK one (so a
    bad step never leaves the project on broken/partial geometry), feed the error
    back to the orchestrator, and let it keep going — the turn is NOT collapsed."""
    provider = ScriptedProvider(
        [
            _plan_turn(tool_use_id="tu-0", steps=["base", "feature"]),
            # Step 1 succeeds -> advances current to a new ok version.
            _tool_turn(tool_use_id="tu-1", name="edit_model", tool_input={"change": "base plate"}),
            # Step 2 fails -> per-step rollback must revert current to step 1's version.
            _tool_turn(
                tool_use_id="tu-2", name="edit_model", tool_input={"change": "broken feature"}
            ),
            # Orchestrator continues (replans/responds) rather than the turn collapsing.
            _text_turn("Reverted the bad step; let me try a different approach."),
        ]
    )
    pipeline = SequencedPipeline([(True, None), (False, "execution: boom")])
    _install(monkeypatch, provider, pipeline=pipeline)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    # Observe rollbacks AS THEY HAPPEN: the per-step policy reverts to last-ok the
    # moment a planned step fails (mid-loop), distinct from the whole-turn
    # settlement revert that also fires at the end of a failed turn.
    reverts: list[int | None] = []
    real_revert = chat._revert_to_last_ok

    async def _spy(store_, project_id):
        result = await real_revert(store_, project_id)
        reverts.append(result)
        return result

    monkeypatch.setattr(chat, "_revert_to_last_ok", _spy)

    events = _stream_chat(client, pid, "make a bracket")
    tool_results = [e for e in events if e["event"] == "tool_result"]
    assert len(tool_results) == 2
    ok_vid = tool_results[0]["version_id"]
    assert ok_vid is not None
    assert tool_results[0]["ok"] is True
    assert tool_results[1]["ok"] is False

    # The failed step triggered a PER-STEP revert (mid-loop) AND the whole-turn
    # settlement revert fires too -> two reverts, both targeting the last-ok vid.
    assert len(reverts) == 2
    assert reverts[0] == ok_vid

    # The orchestrator kept going AFTER the failed step (turn not collapsed).
    kinds = [e["event"] for e in events]
    assert any(e["event"] == "text_delta" and "Reverted" in e["text"] for e in events)
    assert kinds[-1] == "turn_end"
    assert pipeline.run_count == 2  # both steps actually executed

    # Per-step rollback: project's current is back at the last OK version (step 1),
    # NOT left on the failed step's partial geometry.
    assert client.get(f"/projects/{pid}").json()["current_version_id"] == ok_vid


def test_planned_turn_repeated_failures_are_bounded_by_escalation(client, store, monkeypatch):
    """D3: the retry/replan allowed by the rollback policy is bounded — repeated
    failures at the same stage trigger the existing escalation, steering
    the orchestrator off the cycle so it can't loop forever."""
    provider = ScriptedProvider(
        [
            _plan_turn(tool_use_id="tu-0", steps=["base", "feature"]),
            _tool_turn(tool_use_id="tu-1", name="edit_model", tool_input={"change": "try A"}),
            _tool_turn(tool_use_id="tu-2", name="edit_model", tool_input={"change": "try B"}),
            _tool_turn(tool_use_id="tu-3", name="edit_model", tool_input={"change": "try C"}),
            _text_turn("Giving up on this approach."),
        ]
    )
    # Every step fails at the same 'build' stage.
    pipeline = SequencedPipeline([(False, "execution: boom")])
    _install(monkeypatch, provider, pipeline=pipeline)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    _stream_chat(client, pid, "make a bracket")

    # The escalation hint (steer toward ask_clarification) was appended to a failed
    # tool_result the orchestrator saw — bounding the cycle (default threshold 2).
    msgs = _messages(store, pid)
    tool_result_texts = [b.content or "" for b in msgs[1].blocks if b.kind == "tool_result"]
    assert any("ask_clarification" in t for t in tool_result_texts)


def test_unplanned_failed_step_does_not_per_step_revert(client, store, monkeypatch):
    """D3 gating: an ordinary (no plan) turn keeps today's behavior — a failed step
    does NOT trigger the Blueprint per-step revert. Whole-turn settlement still
    guarantees current == last-ok at the end, but no extra per-step rollback runs
    mid-turn (no plan is active)."""
    provider = ScriptedProvider(
        [
            _tool_turn(tool_use_id="tu-1", name="edit_model", tool_input={"change": "base"}),
            _tool_turn(tool_use_id="tu-2", name="edit_model", tool_input={"change": "broken"}),
            _text_turn("Sorry, that failed."),
        ]
    )
    pipeline = SequencedPipeline([(True, None), (False, "execution: boom")])
    _install(monkeypatch, provider, pipeline=pipeline)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    reverts: list[int] = []
    real_revert = chat._revert_to_last_ok

    async def _spy(store_, project_id):
        result = await real_revert(store_, project_id)
        reverts.append(result if result is not None else -1)
        return result

    monkeypatch.setattr(chat, "_revert_to_last_ok", _spy)

    events = _stream_chat(client, pid, "make a bracket")
    tool_results = [e for e in events if e["event"] == "tool_result"]
    ok_vid = tool_results[0]["version_id"]

    # Whole-turn settlement reverts to last-ok ONCE (today's behavior), not an extra
    # per-step revert during the loop.
    assert len(reverts) == 1
    # And the final current is the last-ok version (the successful first step).
    assert client.get(f"/projects/{pid}").json()["current_version_id"] == ok_vid


# --- a regeneration after an edit keeps the assembly it replaces -------------


class _EditsDoNotValidate(StubPipeline):
    """Every edit fails at validation and leaves the code it was given behind."""

    def run(self, intent, export_dir=None, on_progress=None, prior_code=None, **kwargs):
        if prior_code is not None:
            self.assemblies.append(kwargs.get("assembly"))
            return GenerationResult(
                ok=False, intent=intent, code=prior_code, error="validate: syntax error"
            )
        return super().run(intent, export_dir, on_progress, prior_code, **kwargs)


def _build_then_edit_then_regenerate(monkeypatch, pipeline, *, first="edit_model", after=()):
    """Turn one builds a shelf; turn two works on it with ``first``, regenerates
    it, then makes the ``after`` calls."""
    first_input = {"change": "450 mm wide"} if first == "edit_model" else {"params": {"w": 450}}
    turns = [
        _tool_turn(tool_use_id="tu-1", name="generate_model", tool_input={"spec": "a shelf"}),
        _text_turn("Done."),
        _tool_turn(tool_use_id="tu-2", name=first, tool_input=first_input),
        _tool_turn(
            tool_use_id="tu-3",
            name="generate_model",
            tool_input={"spec": "the same shelf, 450 mm wide"},
        ),
        *after,
        _text_turn("Regenerated."),
    ]
    _install(monkeypatch, ScriptedProvider(turns), pipeline=pipeline)


def _forge_on(monkeypatch):
    monkeypatch.setattr(chat.settings, "forge_enabled", True)
    monkeypatch.setattr(chat.settings, "forge_budget", 6)
    monkeypatch.setattr(chat.settings, "forge_candidate_cost", 2)
    monkeypatch.setattr(chat.settings, "forge_min_n", 2)
    monkeypatch.setattr(chat.settings, "forge_max_n", 5)


def _current_record(client, store, pid):
    current = client.get(f"/projects/{pid}").json()["current_version_id"]
    return asyncio.run(store.get_version(current)).built_as_assembly


@pytest.mark.parametrize(
    "pipeline", [StubPipeline, _EditsDoNotValidate], ids=["the edit built", "the edit failed"]
)
def test_a_regeneration_after_an_edit_is_held_to_the_assembly_and_recorded_as_one(
    client, store, monkeypatch, pipeline
):
    """The reload case: the option is off, the model is in pieces, the edit is
    abandoned and the orchestrator regenerates the same design. Judged as a new
    model it was held to the overlap check alone and kept although its parts did
    not fit the bed, recorded as a single solid."""
    monkeypatch.setattr(chat.settings, "assembly_enabled", True)
    stub = pipeline()
    _build_then_edit_then_regenerate(monkeypatch, stub)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    _stream_assembly(client, pid, assembly=True)
    _stream_assembly(client, pid, assembly=False, text="make the bookshelf 450 mm wide")

    built, edited, regenerated = stub.assemblies
    assert regenerated is not None
    assert _current_record(client, store, pid) is True


def test_a_regeneration_after_a_refused_parameter_change_is_recorded_as_the_assembly(
    client, store, monkeypatch
):
    """The operating instructions send a pure resize to ``set_parameters`` first,
    and a refused one leaves the orchestrator to regenerate the same design."""
    monkeypatch.setattr(chat.settings, "assembly_enabled", True)
    monkeypatch.setattr(
        "cadless.agent._default_reparametrize",
        lambda code, overrides, **kw: {"ok": False, "error": "part 1 does not fit", "code": code},
    )
    stub = StubPipeline()
    _build_then_edit_then_regenerate(monkeypatch, stub, first="set_parameters")
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    _stream_assembly(client, pid, assembly=True)
    _stream_assembly(client, pid, assembly=False, text="make the bookshelf 450 mm wide")

    built, regenerated = stub.assemblies
    assert regenerated is not None
    assert _current_record(client, store, pid) is True


def test_a_model_worked_on_after_its_regeneration_is_still_recorded_as_the_assembly(
    client, store, monkeypatch
):
    """The regeneration does not end what the turn inherited: an edit after it
    acts on the same model, still in pieces, and the next turn has to find it so."""
    monkeypatch.setattr(chat.settings, "assembly_enabled", True)
    later_edit = _tool_turn(
        tool_use_id="tu-4", name="edit_model", tool_input={"change": "round the corners"}
    )
    _build_then_edit_then_regenerate(monkeypatch, StubPipeline(), after=[later_edit])
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    _stream_assembly(client, pid, assembly=True)
    _stream_assembly(client, pid, assembly=False, text="make the bookshelf 450 mm wide")

    assert _current_record(client, store, pid) is True


def test_a_regeneration_while_the_switch_is_off_keeps_the_record_of_an_assembly(
    client, store, monkeypatch
):
    """The switch is outermost, and it does not erase: the regeneration is the
    same model, remembered as an assembly exactly as an edit of it would be."""
    monkeypatch.setattr(chat.settings, "assembly_enabled", True)
    _build_then_edit_then_regenerate(monkeypatch, StubPipeline())
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    _stream_assembly(client, pid, assembly=True)
    monkeypatch.setattr(chat.settings, "assembly_enabled", False)
    _stream_assembly(client, pid, assembly=False, text="make the bookshelf 450 mm wide")

    assert _current_record(client, store, pid) is True


def _regenerate(client, pid, *, forge):
    """Send the second turn and return the version its last build made."""
    body = {"message": "make the bookshelf 450 mm wide", "forge": forge, "assembly": False}
    with client.stream("POST", f"/projects/{pid}/chat", json=body) as r:
        events = _events("".join(r.iter_text()))
    return [e for e in events if e["event"] == "tool_result"][-1]["version_id"]


def test_the_losers_of_a_regeneration_race_are_recorded_as_the_assembly_too(
    client, store, monkeypatch
):
    monkeypatch.setattr(chat.settings, "assembly_enabled", True)
    _forge_on(monkeypatch)
    _build_then_edit_then_regenerate(monkeypatch, ForgePipeline())
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    _stream_assembly(client, pid, assembly=True)
    winner_id = _regenerate(client, pid, forge=True)

    candidates = asyncio.run(store.list_candidate_versions(winner_id))
    assert candidates
    assert [c.built_as_assembly for c in candidates] == [True] * len(candidates)


@pytest.mark.parametrize("forge", [False, True], ids=["single run", "forge race"])
def test_a_regeneration_of_a_model_never_built_as_an_assembly_is_not_one(
    client, store, monkeypatch, forge
):
    """Remembering is not a second way to opt in: a model built whole is still one
    solid when the orchestrator regenerates it, winner and losers alike."""
    monkeypatch.setattr(chat.settings, "assembly_enabled", True)
    _forge_on(monkeypatch)
    _build_then_edit_then_regenerate(monkeypatch, ForgePipeline())
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    _stream_assembly(client, pid, assembly=False)
    winner_id = _regenerate(client, pid, forge=forge)

    candidates = asyncio.run(store.list_candidate_versions(winner_id))
    assert bool(candidates) is forge
    winner = asyncio.run(store.get_version(winner_id))
    assert [v.built_as_assembly for v in (winner, *candidates)] == [False] * (1 + len(candidates))
