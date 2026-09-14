"""The assembly stage in the repair loop.

The geometry is scripted rather than built: what is under test here is when the
stage runs, what it does with a verdict, and how the loop ends -- not the
measurement, which ``tests/test_assembly_measure.py`` covers against real solids.
"""

import pytest

from cadless.assembly_check import AssemblyMeasurements
from cadless.config import Settings
from cadless.pipeline import STAGE_PHASES, Pipeline
from cadless.printer_profile import AssemblySpec, BuildVolume
from cadless.worker import ExecResult

GOOD = "from build123d import *\nresult = Box(10, 10, 10)\n"
SPEC = AssemblySpec(volume=BuildVolume(210.0, 200.0, 195.0), clearance_mm=0.2)


class FakeGen:
    def __init__(self, output=GOOD):
        self._output = output
        self.repairs = 0

    def generate(
        self,
        intent,
        grounding=None,
        temperature=None,
        on_token=None,
        images=(),
        on_reading=None,
        assembly=None,
    ):
        return self._output

    def refine(self, intent, prior_code, images=(), on_reading=None):
        return self._output

    def repair(self, intent, code, error, context=None, images=(), assembly=None):
        self.repairs += 1
        self.last_repair_error = error
        return self._output


def _sound() -> AssemblyMeasurements:
    return AssemblyMeasurements(
        part_bboxes=[[100.0, 100.0, 50.0], [100.0, 100.0, 50.0]],
        overlaps=[],
        gaps=[[0, 1, 0.2]],
        order=[0, 1],
    )


def _overlapping() -> AssemblyMeasurements:
    return AssemblyMeasurements(
        part_bboxes=[[100.0, 100.0, 50.0], [100.0, 100.0, 50.0]],
        overlaps=[[0, 1, 40.0]],
        gaps=[[0, 1, 0.0]],
        order=[0, 1],
    )


def _exec(measurements=None) -> ExecResult:
    return ExecResult(
        ok=True,
        volume=1000.0,
        bbox=(10.0, 10.0, 10.0),
        part_count=2,
        stl_path="/tmp/model.stl",
        assembly=measurements,
    )


def _stub_run_code(monkeypatch, measurements, captured=None):
    def fake(code, *, export_dir=None, export_scale=1.0, check_assembly=False, config=None):
        if captured is not None:
            captured["check_assembly"] = check_assembly
        return _exec(measurements if check_assembly else None)

    monkeypatch.setattr("cadless.pipeline.run_code", fake)


def _stages(events):
    return [(e.get("phase"), e.get("status")) for e in events if e.get("event") == "stage"]


def _run(gen, measurements, monkeypatch, *, assembly=SPEC, tries=3, captured=None):
    _stub_run_code(monkeypatch, measurements, captured)
    events = []
    result = Pipeline(generator=gen, config=Settings(repair_max_attempts=tries)).run(
        "a bracket", on_progress=events.append, assembly=assembly
    )
    return result, events


# --- the stage exists and is reachable ------------------------------------


def test_the_assembly_phase_is_in_the_progress_vocabulary():
    # Emitting a phase outside this tuple is what tests/test_sse_progress.py
    # catches, so the stage below has to be declared here to be emittable at all.
    assert "assembly" in STAGE_PHASES


def test_a_sound_split_passes_and_the_stage_reports_ok(monkeypatch):
    result, events = _run(FakeGen(), _sound(), monkeypatch)
    assert result.ok
    assert ("assembly", "ok") in _stages(events)


def test_a_turn_that_asked_for_an_assembly_asks_the_worker_to_measure(monkeypatch):
    captured = {}
    _run(FakeGen(), _sound(), monkeypatch, captured=captured)
    assert captured["check_assembly"] is True


def test_a_turn_that_did_not_ask_never_runs_the_stage(monkeypatch):
    captured = {}
    result, events = _run(FakeGen(), _sound(), monkeypatch, assembly=None, captured=captured)
    assert result.ok
    assert captured["check_assembly"] is False
    assert not [phase for phase, _ in _stages(events) if phase == "assembly"]


def test_a_single_part_result_never_runs_the_stage(monkeypatch):
    # The worker reports nothing to check when there is only one solid, and a lone
    # part against the build volume is print_fit's question, not this stage's.
    result, events = _run(FakeGen(), None, monkeypatch)
    assert result.ok
    assert not [phase for phase, _ in _stages(events) if phase == "assembly"]


# --- a failure is an ordinary repair signal -------------------------------


def test_a_bad_split_forces_a_repair_while_budget_remains(monkeypatch):
    gen = FakeGen()
    result, events = _run(gen, _overlapping(), monkeypatch, tries=3)
    assert gen.repairs >= 1
    assert ("assembly", "error") in _stages(events)
    assert "assembly:" in gen.last_repair_error


def test_the_repair_signal_names_the_check_that_failed(monkeypatch):
    gen = FakeGen()
    _run(gen, _overlapping(), monkeypatch, tries=3)
    assert "interior volume" in gen.last_repair_error


def test_the_failing_attempt_is_recorded_under_its_own_stage(monkeypatch):
    result, _ = _run(FakeGen(), _overlapping(), monkeypatch, tries=2)
    assert any(a.stage == "assembly" for a in result.attempts)


# --- the last attempt: refused, not presented -----------------------------


def test_a_bad_split_on_the_last_attempt_is_refused_rather_than_delivered(monkeypatch):
    # This is the whole difference from the assert stage next door, which is gated
    # n < max_tries and so never looks at the build that actually gets returned.
    result, _ = _run(FakeGen(), _overlapping(), monkeypatch, tries=1)
    assert not result.ok
    assert "assembly:" in result.error


def test_a_persistently_bad_split_ends_the_loop_refused(monkeypatch):
    gen = FakeGen()
    result, _ = _run(gen, _overlapping(), monkeypatch, tries=3)
    assert not result.ok
    assert gen.repairs == 2  # one generation plus two repairs, then refused


def test_the_error_says_the_budget_ended_it_rather_than_a_pass(monkeypatch):
    result, _ = _run(FakeGen(), _overlapping(), monkeypatch, tries=1)
    assert result.error is not None
    assert "not accepted" in result.error


# --- what the result carries ----------------------------------------------


def test_a_passing_result_carries_the_assembly_order(monkeypatch):
    # P4 states this order in the guide it writes, so it has to survive the loop.
    result, _ = _run(FakeGen(), _sound(), monkeypatch)
    assert result.assembly is not None
    assert result.assembly["ok"] is True
    assert result.assembly["order"] == [0, 1]


def test_a_result_from_a_turn_that_did_not_ask_carries_nothing(monkeypatch):
    result, _ = _run(FakeGen(), _sound(), monkeypatch, assembly=None)
    assert result.assembly is None


def test_an_unrunnable_check_refuses_rather_than_passing(monkeypatch):
    unchecked = AssemblyMeasurements(
        part_bboxes=[[100.0, 100.0, 50.0], [100.0, 100.0, 50.0]],
        gaps=[[0, 1, 0.2]],
        order=[0, 1],
        unchecked=["assembly order: the search ran out of collision probes"],
    )
    result, _ = _run(FakeGen(), unchecked, monkeypatch, tries=1)
    assert not result.ok
    assert "could not be established" in result.error


def test_a_verdict_never_outlives_the_attempt_it_was_taken_for(monkeypatch):
    # First attempt builds and passes the check; the second fails to execute, so
    # it never reaches the check at all. Carrying the first verdict forward would
    # attach a pass to a build that is not the one being returned.
    calls = {"n": 0}

    def fake(code, *, export_dir=None, export_scale=1.0, check_assembly=False, config=None):
        calls["n"] += 1
        # Attempt 1 builds and is measured (and fails the check, so the loop goes
        # round); attempt 2 never executes, so nothing measures it.
        if calls["n"] == 1:
            return _exec(_overlapping())
        return ExecResult(ok=False, error="OCCT boom")

    monkeypatch.setattr("cadless.pipeline.run_code", fake)
    result = Pipeline(generator=FakeGen(), config=Settings(repair_max_attempts=2)).run(
        "a bracket", assembly=SPEC
    )
    assert not result.ok
    # The returned build is the one that failed to execute, and nothing measured it.
    assert result.assembly is None


@pytest.mark.parametrize("tries", [1, 2, 3])
def test_the_loop_is_bounded_however_bad_the_split(monkeypatch, tries):
    gen = FakeGen()
    result, _ = _run(gen, _overlapping(), monkeypatch, tries=tries)
    assert not result.ok
    assert result.attempt_count <= tries
