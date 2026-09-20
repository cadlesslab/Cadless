"""The assembly stage in the repair loop.

The geometry is scripted rather than built: what is under test here is when the
stage runs, what it does with a verdict, and how the loop ends -- not the
measurement, which ``tests/test_assembly_measure.py`` covers against real solids.
"""

import json

import pytest

from cadless.agent import Agent, ToolContext
from cadless.assembly_check import AssemblyMeasurements
from cadless.config import Settings
from cadless.llm.providers.fake import FakeChatProvider
from cadless.llm.types import ContentBlock
from cadless.pipeline import STAGE_PHASES, Pipeline
from cadless.printer_profile import AssemblySpec, BuildVolume
from cadless.prompts import CodeGenerator, _assembly_edit_rules, _assembly_rules
from cadless.worker import ExecResult

GOOD = "from build123d import *\nresult = Box(10, 10, 10)\n"
SPEC = AssemblySpec(volume=BuildVolume(210.0, 200.0, 195.0), clearance_mm=0.2)


class FakeGen:
    def __init__(self, output=GOOD):
        self._output = output
        self.repairs = 0
        self.last_refine_assembly = None

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

    def refine(self, intent, prior_code, images=(), on_reading=None, assembly=None):
        self.last_refine_assembly = assembly
        return self._output

    def repair(self, intent, code, error, context=None, images=(), assembly=None):
        self.repairs += 1
        self.last_repair_error = error
        return self._output


# Never validates: `os` is a disallowed import, so a run whose every output is
# this one fails at the validate stage and never reaches the worker.
NEVER_VALIDATES = "```python\nimport os\nresult = os\n```"
TWO_PART = "from build123d import *\nresult = Compound([Box(10,10,10), Box(10,10,10)])\n"


class _RecordingProvider(FakeChatProvider):
    """Records the user message of every completion, so a test can read the
    prompt a round actually sent.

    The doubles above replace the generator, which is what makes them cheap --
    and also what makes them blind here: a fake generator composes no prompt, so
    no assertion about framing can be made through one. This sits a layer lower,
    under a real ``CodeGenerator``, which is the only way the framing of a round
    is observable from a pipeline test.
    """

    def __init__(self):
        super().__init__()
        self.calls: list[str] = []

    def complete(self, *, model, system, user, **kw):
        self.calls.append(user)
        return NEVER_VALIDATES


def _sound() -> AssemblyMeasurements:
    return AssemblyMeasurements(
        part_bboxes=[[100.0, 100.0, 50.0], [100.0, 100.0, 50.0]],
        overlaps=[],
        gaps=[[0, 1, 0.2]],
        order=[0, 1],
        # The second part is the one left standing, so the search recorded no
        # heading for it -- nothing was left that could have blocked one.
        releases=[[0.0, 0.0, 1.0], []],
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


def _run(
    gen, measurements, monkeypatch, *, assembly=SPEC, tries=3, captured=None, guide_writer=None
):
    _stub_run_code(monkeypatch, measurements, captured)
    events = []
    result = Pipeline(
        generator=gen,
        config=Settings(repair_max_attempts=tries),
        guide_writer=guide_writer,
    ).run("a bracket", on_progress=events.append, assembly=assembly)
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


def test_an_edit_to_an_assembly_reaches_the_generator_with_the_spec(monkeypatch):
    """``prior_code`` routes the turn through refine instead of generate, and the
    spec has to survive that turn. Losing it here is invisible downstream: one
    solid is an ordinary result, so nothing further in the run refuses it."""
    _stub_run_code(monkeypatch, _sound())
    gen = FakeGen()

    Pipeline(generator=gen, config=Settings(repair_max_attempts=3)).run(
        "make it taller", prior_code="result = Box(5,5,5)", assembly=SPEC
    )

    assert gen.last_refine_assembly is SPEC


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


def test_an_assembly_turn_that_measured_nothing_says_so(monkeypatch):
    # "Asked and nothing came back" and "never asked" both used to leave the
    # result carrying None, so a reader could not tell a turn whose model failed
    # to split from one that never wanted a split.
    result, _ = _run(FakeGen(), None, monkeypatch)
    assert result.assembly is not None
    assert result.assembly["measured"] is False
    assert result.assembly["ok"] is None


def test_a_turn_that_never_asked_is_distinguishable_from_one_that_did(monkeypatch):
    result, _ = _run(FakeGen(), None, monkeypatch, assembly=None)
    assert result.assembly is None


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


# --- what a repair round is told, and by which round it is framed ---------


def test_a_repair_beneath_an_edit_is_not_asked_to_design_the_split():
    """An edit acts on a model whose split already exists, and a repair under it
    is still that edit -- fixing what the edit produced, not answering a question
    about how the thing should come apart. Framing that round with the whole
    design brief puts "split it into the FEWEST parts" above a request to fix a
    typo, which is the contradiction the edit prompt itself was cleared of.

    Two assertions carry the test and the first is what stops the second passing
    vacuously: dropping the spec from the repair path altogether would satisfy
    every absence below while losing the bed and the clearance the round must
    still respect. The whole-block assertion is the one that cannot drift --
    rewording the rules moves it too, whereas the literal clauses beneath it
    would quietly stop matching anything and pass.
    """
    prov = _RecordingProvider()

    Pipeline(
        generator=CodeGenerator(provider=prov),
        config=Settings(repair_max_attempts=2),
    ).run("make it 20 mm taller", prior_code=TWO_PART, assembly=SPEC)

    assert len(prov.calls) == 2, "expected one edit round followed by one repair round"
    repair = prov.calls[1]
    assert _assembly_edit_rules(SPEC) in repair
    assert _assembly_rules(SPEC) not in repair
    assert "FEWEST parts" not in repair
    assert "where a cut does least harm" not in repair


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


def test_an_edit_that_breaks_the_assembly_is_refused_too(monkeypatch):
    """The refusal above, reached the way a user reaches it -- through the edit
    tool rather than by calling the pipeline directly.

    Both halves are needed here and neither is enough alone, which is why this is
    driven from the agent and not from ``Pipeline.run``. The stage refuses a bad
    split whichever mode produced it, so a pipeline-level edit test would pass
    equally well while the tool above it dropped the spec; and the tool handing
    the spec down proves only that it arrived. Withhold it at the tool and the
    worker is never asked to measure, the stage has nothing to look at, and this
    overlapping result comes back ``ok`` -- an edit turning a sound assembly into
    an unassemblable one and being accepted, immediately after a fresh build of
    the same model would have been refused.
    """
    _stub_run_code(monkeypatch, _overlapping())
    pipeline = Pipeline(generator=FakeGen(), config=Settings(repair_max_attempts=1))
    ctx = ToolContext(pipeline=pipeline, current_code="result = Box(5,5,5)", assembly=SPEC)
    edit = ContentBlock.of_tool_use(id="tu-1", name="edit_model", input={"change": "taller"})

    _, payload = Agent(provider=FakeChatProvider(), model="fake-model")._execute_one(edit, ctx)

    assert payload["ok"] is False
    assert "assembly:" in payload["error"]


# --- what the result carries ----------------------------------------------


def test_a_passing_result_carries_the_assembly_order(monkeypatch):
    # P4 states this order in the guide it writes, so it has to survive the loop.
    result, _ = _run(FakeGen(), _sound(), monkeypatch)
    assert result.assembly is not None
    assert result.assembly["ok"] is True
    assert result.assembly["order"] == [0, 1]


def test_a_passing_result_carries_the_joints_and_the_release_headings(monkeypatch):
    # A guide names each part's neighbours and shows each part moving the way it
    # comes out. Both are measured while the split is checked, and both stopped at
    # the worker boundary -- leaving anything downstream to derive them again from
    # numbers it no longer has.
    result, _ = _run(FakeGen(), _sound(), monkeypatch)
    assert result.assembly is not None
    assert result.assembly["joints"] == [[1], [0]]
    assert result.assembly["releases"] == [[0.0, 0.0, 1.0], []]


def test_what_the_result_carries_survives_the_trip_to_the_model(monkeypatch):
    # This dict is serialised onward whole. Anything in it that json.dumps
    # silently retypes would reach the far side as something else.
    result, _ = _run(FakeGen(), _sound(), monkeypatch)
    assert json.loads(json.dumps(result.assembly)) == result.assembly


def test_a_result_from_a_turn_that_did_not_ask_carries_nothing(monkeypatch):
    result, _ = _run(FakeGen(), _sound(), monkeypatch, assembly=None)
    assert result.assembly is None


# --- the guide ------------------------------------------------------------


def test_an_accepted_split_is_described(monkeypatch):
    result, _ = _run(FakeGen(), _sound(), monkeypatch)
    assert result.ok
    assert result.guide is not None
    assert result.guide["parts"] == ["part 1", "part 2"]
    assert result.guide["steps"] == ["Start with part 1.", "Fit part 2 to part 1."]
    # No export directory in these runs, so there is nowhere to draw to. The
    # written steps do not depend on the pictures.
    assert result.guide["frames"] == 0


def test_the_guide_reaches_the_progress_vocabulary(monkeypatch):
    _, events = _run(FakeGen(), _sound(), monkeypatch)
    phases = [e.get("phase") for e in events if e.get("event") == "stage"]
    assert "guide" in phases
    assert "guide" in STAGE_PHASES


def test_a_refused_split_is_never_described(monkeypatch):
    # A refused turn leaves the loop before the guide stage is reached at all,
    # so what this pins is the route rather than a condition: should the refusal
    # path ever start returning through the accepted one, a guide to parts
    # nobody receives would go out reading as though the build had succeeded.
    result, _ = _run(FakeGen(), _overlapping(), monkeypatch, tries=1)
    assert not result.ok
    assert result.guide is None


def test_a_verdict_that_did_not_pass_is_not_described_even_holding_an_order():
    # The condition behind the route above, pinned on its own because no run
    # reaches it today: every refusal currently returns by another path. Called
    # directly, a verdict carrying an order it did not earn must still describe
    # nothing -- otherwise the one line standing between a failed check and a
    # guide is only ever exercised by accident.
    guide = Pipeline(generator=FakeGen())._guide(
        None,
        "a bracket",
        GOOD,
        {"ok": False, "order": [0, 1], "joints": [[1], [0]], "measured": True},
        None,
        1,
    )
    assert guide is None


def test_a_turn_that_did_not_ask_is_never_described(monkeypatch):
    result, _ = _run(FakeGen(), _sound(), monkeypatch, assembly=None)
    assert result.ok
    assert result.guide is None


def test_a_single_part_build_is_not_an_assembly_and_gets_no_guide(monkeypatch):
    lone = AssemblyMeasurements(part_bboxes=[[10.0, 10.0, 10.0]], gaps=[], order=[0])
    result, _ = _run(FakeGen(), lone, monkeypatch)
    assert result.ok
    assert result.guide is None


def test_a_writer_that_fails_costs_the_guide_and_not_the_build(monkeypatch):
    # The parts are built, measured and accepted by this point. Losing the
    # description must not lose them.
    class _AngryWriter:
        def write(self, *a, **kw):
            raise RuntimeError("no provider")

    result, events = _run(FakeGen(), _sound(), monkeypatch, guide_writer=_AngryWriter())
    assert result.ok
    assert result.guide is None
    errors = [e for e in events if e.get("event") == "stage" and e.get("phase") == "guide"]
    assert any(e.get("status") == "error" for e in errors)


def test_the_writer_is_given_the_measured_order_and_joints(monkeypatch):
    seen = {}

    class _Spy:
        def write(self, intent, code, order, joints):
            seen["order"] = order
            seen["joints"] = joints
            from cadless.guide_writer import plain_guide

            return plain_guide(order, joints)

    _run(FakeGen(), _sound(), monkeypatch, guide_writer=_Spy())
    assert seen["order"] == [0, 1]
    assert seen["joints"] == [[1], [0]]


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


# --- end to end, through the real worker ----------------------------------
#
# Everything above scripts the geometry. These run the whole loop for real: the
# validator, a subprocess, OCCT, the measurement and the policy. Only the model
# is scripted, because a live one costs money and answers differently each time
# -- so what these establish is that the mechanism works on a real turn's path,
# not that a real model produces a good split.

_TWO_PARTS = "from build123d import *\nresult = Compound(children=[{a}, {b}])\n"
CLEARED = _TWO_PARTS.format(a="Box(20, 20, 10)", b="Pos(20.2, 0, 0) * Box(20, 20, 10)")
OVERLAPPING = _TWO_PARTS.format(a="Box(20, 20, 10)", b="Pos(19, 0, 0) * Box(20, 20, 10)")
TOO_BIG = _TWO_PARTS.format(a="Box(400, 20, 10)", b="Pos(20.2, 0, 0) * Box(20, 20, 10)")


@pytest.mark.build123d
def test_a_sound_split_survives_the_whole_loop():
    result = Pipeline(generator=FakeGen(CLEARED), config=Settings(repair_max_attempts=2)).run(
        "a two-part bracket", assembly=SPEC
    )
    assert result.ok, result.error
    assert result.assembly is not None and result.assembly["ok"] is True
    assert sorted(result.assembly["order"]) == [0, 1]


@pytest.mark.build123d
def test_a_deliberately_bad_split_is_refused_and_produces_a_repair_round():
    # The generator keeps emitting the same overlapping pair, so the loop repairs
    # once and then refuses rather than handing the parts over.
    gen = FakeGen(OVERLAPPING)
    result = Pipeline(generator=gen, config=Settings(repair_max_attempts=2)).run(
        "a two-part bracket", assembly=SPEC
    )
    assert not result.ok
    assert "assembly:" in result.error
    assert gen.repairs == 1, "the failure did not produce another attempt"
    assert "interior volume" in result.error


@pytest.mark.build123d
def test_a_part_that_will_not_fit_the_bed_is_refused_end_to_end():
    result = Pipeline(generator=FakeGen(TOO_BIG), config=Settings(repair_max_attempts=1)).run(
        "a two-part bracket", assembly=SPEC
    )
    assert not result.ok
    assert "does not fit" in result.error


@pytest.mark.build123d
def test_the_same_split_is_accepted_when_the_turn_did_not_ask_for_an_assembly():
    # The overlapping pair is only a defect against the assembly contract. With
    # the option off it is an ordinary multi-solid build and must behave exactly
    # as it did before this stage existed.
    result = Pipeline(generator=FakeGen(OVERLAPPING), config=Settings(repair_max_attempts=1)).run(
        "two blocks", assembly=None
    )
    assert result.ok, result.error
    assert result.assembly is None
