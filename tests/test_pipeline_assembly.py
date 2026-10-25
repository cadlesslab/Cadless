"""The assembly stage in the repair loop.

The geometry is scripted rather than built: what is under test here is when the
stage runs, what it does with a verdict, and how the loop ends -- not the
measurement, which ``tests/test_assembly_measure.py`` covers against real solids.
"""

import json
from pathlib import Path

import pytest

from cadless.agent import Agent, ToolContext
from cadless.assembly_check import AssemblyMeasurements
from cadless.config import Settings
from cadless.llm.providers import StreamChunk
from cadless.llm.providers.fake import FakeChatProvider
from cadless.llm.types import ContentBlock, StreamEvent
from cadless.pipeline import STAGE_PHASES, Pipeline, critique_subject
from cadless.printer_profile import AssemblySpec, BuildVolume
from cadless.prompts import CodeGenerator, _assembly_edit_rules, _assembly_rules
from cadless.vlm_critique import Critique
from cadless.worker import ExecResult

GOOD = "from build123d import *\nresult = Box(10, 10, 10)\n"
SPEC = AssemblySpec(volume=BuildVolume(210.0, 200.0, 195.0), clearance_mm=0.2)


class FakeGen:
    def __init__(self, output=GOOD):
        self._output = output
        self.repairs = 0
        self.last_refine_assembly = None
        self.repair_may_resplits: list[bool] = []
        self.repair_assemblies: list = []

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

    def repair(self, intent, code, error, context=None, images=(), assembly=None, may_resplit=True):
        self.repairs += 1
        self.last_repair_error = error
        self.repair_may_resplits.append(may_resplit)
        self.repair_assemblies.append(assembly)
        return self._output


# Never validates: `os` is a disallowed import, so a run whose every output is
# this one fails at the validate stage and never reaches the worker.
NEVER_VALIDATES = "```python\nimport os\nresult = os\n```"
TWO_PART = "from build123d import *\nresult = Compound([Box(10,10,10), Box(10,10,10)])\n"


class _RecordingProvider(FakeChatProvider):
    """Records the user message of every turn, so a test can read the prompt a
    round actually sent.

    The doubles above replace the generator, which is what makes them cheap --
    and also what makes them blind here: a fake generator composes no prompt, so
    no assertion about framing can be made through one. This sits a layer lower,
    under a real ``CodeGenerator``, which is the only way the framing of a round
    is observable from a pipeline test.
    """

    def __init__(self):
        super().__init__()
        # Not ``calls``: the base class keeps its own list of dicts under that
        # name, and a reader who knows the base would take this for that list.
        self.user_messages: list[str] = []

    def stream_turn(self, *, model, system, messages, tools, params):
        self.user_messages.append("".join(b.text or "" for b in messages[-1].content if b.text))
        yield StreamChunk(StreamEvent.TEXT_DELTA, {"text": NEVER_VALIDATES})


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


def _exec(measurements=None, *, bbox=(10.0, 10.0, 10.0), part_count=None) -> ExecResult:
    # The worker counts the solids it found: two where it measured a split, and
    # one where there was nothing to measure, unless a test says otherwise.
    if part_count is None:
        part_count = 2 if measurements is not None else 1
    return ExecResult(
        ok=True,
        volume=1000.0,
        bbox=bbox,
        part_count=part_count,
        stl_path="/tmp/model.stl",
        assembly=measurements,
    )


def _stub_run_code(monkeypatch, measurements, captured=None, *, bbox=None, part_count=None):
    def fake(code, *, export_dir=None, export_scale=1.0, check_assembly=False, config=None):
        if captured is not None:
            captured["check_assembly"] = check_assembly
        extra = {} if bbox is None else {"bbox": bbox}
        return _exec(measurements if check_assembly else None, part_count=part_count, **extra)

    monkeypatch.setattr("cadless.pipeline.run_code", fake)


def _stages(events):
    return [(e.get("phase"), e.get("status")) for e in events if e.get("event") == "stage"]


def _run(
    gen,
    measurements,
    monkeypatch,
    *,
    assembly=SPEC,
    tries=3,
    captured=None,
    guide_writer=None,
    export_dir=None,
    bbox=None,
    part_count=None,
    export_scale=1.0,
):
    _stub_run_code(monkeypatch, measurements, captured, bbox=bbox, part_count=part_count)
    events = []
    result = Pipeline(
        generator=gen,
        config=Settings(repair_max_attempts=tries),
        guide_writer=guide_writer,
    ).run(
        "a bracket",
        on_progress=events.append,
        assembly=assembly,
        export_dir=None if export_dir is None else str(export_dir),
        export_scale=export_scale,
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


def test_a_turn_that_did_not_ask_still_has_its_parts_measured(monkeypatch):
    """The worker measures only a build of several parts, so asking costs a
    one-part build nothing; whether the turn asked decides how it is judged."""
    captured = {}
    result, events = _run(FakeGen(), _sound(), monkeypatch, assembly=None, captured=captured)
    assert result.ok
    assert captured["check_assembly"] is True
    assert ("assembly", "ok") in _stages(events)


def test_a_single_part_that_fits_never_runs_the_stage(monkeypatch):
    # The worker reports nothing to check when there is only one solid, and a lone
    # part that fits the bed has nothing else this stage judges.
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


# --- a lone part on a turn that asked is held to the bed --------------------


def test_a_lone_part_larger_than_the_bed_on_an_assembly_turn_is_refused(monkeypatch):
    # The model gave up on the split and returned the whole board as one solid.
    gen = FakeGen()
    result, events = _run(gen, None, monkeypatch, bbox=(300.0, 250.0, 18.0), tries=2)
    assert not result.ok
    assert ("assembly", "error") in _stages(events)
    assert result.assembly["measured"] is True
    assert result.assembly["ok"] is False
    assert "2 along its width and 2 along its depth" in gen.last_repair_error


def test_a_lone_part_larger_than_the_bed_is_kept_when_no_assembly_was_asked(monkeypatch):
    # print_fit's question, as before: nobody asked for a split.
    result, events = _run(FakeGen(), None, monkeypatch, assembly=None, bbox=(300.0, 250.0, 18.0))
    assert result.ok
    assert not [phase for phase, _ in _stages(events) if phase == "assembly"]


def test_a_lone_part_is_judged_in_millimetres(monkeypatch):
    # The worker's box is in authoring units; a model authored in metres at
    # export_scale 1000 is the same board.
    result, _ = _run(
        FakeGen(), None, monkeypatch, bbox=(0.3, 0.25, 0.018), export_scale=1000.0, tries=1
    )
    assert not result.ok
    assert "300 x 250 x 18" in result.assembly["failures"][0]


def test_a_multi_part_build_that_measured_nothing_keeps_the_not_measured_verdict(monkeypatch):
    # Its box spans every solid, so judging it as one part would refuse pieces
    # that each fit; that case stays "not measured".
    result, _ = _run(FakeGen(), None, monkeypatch, bbox=(300.0, 250.0, 18.0), part_count=2)
    assert result.ok
    assert result.assembly["measured"] is False


def test_a_build_whose_solids_could_not_be_counted_keeps_the_not_measured_verdict(monkeypatch):
    # No part count is the worker failing to read the solids at all; the box then
    # spans whatever it exported and says nothing about any one part.
    uncounted = ExecResult(
        ok=True,
        volume=1000.0,
        bbox=(300.0, 250.0, 18.0),
        part_count=None,
        stl_path="/tmp/model.stl",
        assembly=None,
    )
    monkeypatch.setattr("cadless.pipeline.run_code", lambda code, **kw: uncounted)
    result = Pipeline(generator=FakeGen(), config=Settings(repair_max_attempts=1)).run(
        "a bracket", assembly=SPEC
    )
    assert result.ok
    assert result.assembly["measured"] is False


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

    The presence assertion is what stops the absences passing vacuously: dropping
    the spec from the repair path altogether would satisfy every one of them while
    losing the bed and the clearance the round must still respect. The whole-block
    assertion is the one that cannot drift -- rewording the rules moves it too,
    whereas the literal clauses beneath it would quietly stop matching anything
    and pass.
    """
    prov = _RecordingProvider()

    Pipeline(
        generator=CodeGenerator(provider=prov),
        config=Settings(repair_max_attempts=2),
    ).run("make it 20 mm taller", prior_code=TWO_PART, assembly=SPEC)

    assert len(prov.user_messages) == 2, "expected one edit round then one repair round"
    repair = prov.user_messages[1]
    assert _assembly_edit_rules(SPEC) in repair
    assert _assembly_rules(SPEC) not in repair
    assert "FEWEST parts" not in repair
    assert "where a cut does least harm" not in repair


def test_a_turn_with_empty_prior_code_frames_its_repairs_as_the_fresh_run_it_is():
    """Empty prior code takes the fresh-generation branch, so its repairs have to
    be framed as a fresh run's.

    Every reading of ``prior_code`` in the run loop has to agree, and under an
    identity test they do not: the turn would be handed the whole brief on its
    first round and the edit constraints on every repair of it, arguing with
    itself one round later. No caller passes an empty string today; the assertion
    is that whoever does gets one turn rather than two halves of different ones.
    """
    prov = _RecordingProvider()

    Pipeline(
        generator=CodeGenerator(provider=prov),
        config=Settings(repair_max_attempts=2),
    ).run("a shelf", prior_code="", assembly=SPEC)

    assert len(prov.user_messages) == 2, "expected one fresh round then one repair round"
    assert _assembly_rules(SPEC) in prov.user_messages[1]


def test_a_repair_forced_by_the_assembly_check_may_resplit_even_under_an_edit(monkeypatch):
    """The exception to the test above, and the reason it is an exception rather
    than a special case someone forgot.

    An edit that breaks the assembly escalates here: the check fires, forces a
    repair, and that repair is the turn's one chance to cut the model
    differently. Framed by the constraints alone it could only try the same split
    again, so the turn would keep failing the same check until the budget ran out
    and then refuse -- a failure mode indistinguishable from a model that simply
    could not do it.

    The assertion is worth more than it looks because the turn driving it is an
    edit: this site is overriding what the rest of the turn computed, so wiring
    it like its siblings records False here and goes red.
    """
    _stub_run_code(monkeypatch, _overlapping())
    gen = FakeGen()

    Pipeline(generator=gen, config=Settings(repair_max_attempts=3)).run(
        "make it taller", prior_code="result = Box(5,5,5)", assembly=SPEC
    )

    assert gen.repair_may_resplits, "the assembly check never forced a repair"
    assert all(gen.repair_may_resplits)


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


def test_a_multi_part_build_that_did_not_ask_carries_its_verdict(monkeypatch):
    result, _ = _run(FakeGen(), _sound(), monkeypatch, assembly=None)
    assert result.assembly is not None
    assert result.assembly["measured"] is True
    assert result.assembly["ok"] is True
    assert result.assembly["order"] == [0, 1]


def _loose_and_oversized() -> AssemblyMeasurements:
    return AssemblyMeasurements(
        part_bboxes=[[999.0, 100.0, 50.0], [100.0, 100.0, 50.0]],
        overlaps=[],
        gaps=[[0, 1, 30.0]],
        order=[0, 1],
        releases=[[0.0, 0.0, 1.0], []],
    )


def test_a_build_that_did_not_ask_is_not_held_to_the_bed_or_to_touching(monkeypatch):
    gen = FakeGen()
    result, _ = _run(gen, _loose_and_oversized(), monkeypatch, assembly=None)
    assert result.ok
    assert result.assembly["ok"] is True
    assert gen.repairs == 0


def test_a_turn_that_asked_is_still_held_to_the_bed(monkeypatch):
    result, _ = _run(FakeGen(), _loose_and_oversized(), monkeypatch, tries=1)
    assert not result.ok
    assert "does not fit" in result.error


def test_an_unasked_shared_volume_is_repaired_without_the_bed_split_brief(monkeypatch):
    gen = FakeGen()
    _run(gen, _overlapping(), monkeypatch, assembly=None, tries=3)
    assert gen.repairs >= 1
    assert "share" in gen.last_repair_error
    assert gen.repair_assemblies == [None] * gen.repairs


def test_an_unasked_failure_on_the_last_attempt_is_handed_over_with_its_verdict(monkeypatch):
    """Nobody asked for an assembly, so the model asked for is handed over with
    its verdict rather than refused."""
    result, _ = _run(FakeGen(), _overlapping(), monkeypatch, assembly=None, tries=1)
    assert result.ok
    assert result.assembly["ok"] is False
    assert any("share" in f for f in result.assembly["failures"])
    assert result.guide is None


def test_an_unasked_verdict_that_could_not_be_established_is_not_repaired(monkeypatch):
    """A check that could not be established found no fault for a repair to fix."""
    reason = "shared volume between part 1 and part 2: the intersection could not be computed"
    spent = AssemblyMeasurements(
        part_bboxes=[[100.0, 100.0, 50.0], [100.0, 100.0, 50.0]],
        overlaps=[],
        gaps=[[0, 1, 0.2]],
        order=[0, 1],
        unchecked=[reason],
    )
    gen = FakeGen()
    result, _ = _run(gen, spent, monkeypatch, assembly=None, tries=3)
    assert gen.repairs == 0
    assert result.ok
    assert result.assembly["ok"] is False
    assert result.assembly["unchecked"] == [reason]


def test_a_captive_part_is_not_repaired_when_the_turn_did_not_ask(monkeypatch):
    """A chain or a ball in a cage has no order to come apart in, by design."""
    captive = AssemblyMeasurements(
        part_bboxes=[[20.0, 20.0, 4.0], [20.0, 20.0, 4.0]],
        overlaps=[],
        gaps=[[0, 1, 2.0]],
        order=None,
        trapped=[0, 1],
    )
    gen = FakeGen()
    result, _ = _run(gen, captive, monkeypatch, assembly=None, tries=3)
    assert gen.repairs == 0
    assert result.ok
    assert result.assembly["ok"] is True


def test_the_verdict_says_which_checks_the_build_was_held_to(monkeypatch):
    asked, _ = _run(FakeGen(), _sound(), monkeypatch)
    unasked, _ = _run(FakeGen(), _sound(), monkeypatch, assembly=None)
    assert asked.assembly["checks"] == ["fit", "mating", "order", "overlap"]
    assert unasked.assembly["checks"] == ["overlap"]


def test_an_unasked_failure_is_reported_as_a_failed_stage(monkeypatch):
    _, events = _run(FakeGen(), _overlapping(), monkeypatch, assembly=None, tries=1)
    assert ("assembly", "error") in _stages(events)


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


def test_a_multi_part_build_that_did_not_ask_is_described_too(monkeypatch):
    result, _ = _run(FakeGen(), _sound(), monkeypatch, assembly=None)
    assert result.ok
    assert result.guide is not None
    assert result.guide["steps"] == ["Start with part 1.", "Fit part 2 to part 1."]


class _CountingWriter:
    def __init__(self):
        self.calls = 0

    def write(self, intent, code, order, joints, names=None):
        from cadless.guide_writer import plain_guide

        self.calls += 1
        return plain_guide(order, joints, names)


def test_only_a_turn_that_asked_has_its_parts_named_by_the_writer(monkeypatch):
    """Measuring every build of several parts adds no model call to a turn that
    did not ask for an assembly."""
    unasked = _CountingWriter()
    _run(FakeGen(), _sound(), monkeypatch, assembly=None, guide_writer=unasked)
    asked = _CountingWriter()
    _run(FakeGen(), _sound(), monkeypatch, guide_writer=asked)
    assert unasked.calls == 0
    assert asked.calls == 1


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
        def write(self, intent, code, order, joints, names=None):
            seen["order"] = order
            seen["joints"] = joints
            from cadless.guide_writer import plain_guide

            return plain_guide(order, joints)

    _run(FakeGen(), _sound(), monkeypatch, guide_writer=_Spy())
    assert seen["order"] == [0, 1]
    assert seen["joints"] == [[1], [0]]


# --- the motion beside the guide ------------------------------------------
#
# The viewport plays an accepted split from a file written beside the exports:
# the order the parts go together in, the heading each one came out along, and
# how far to move them. The stub builds write no model files, so the guide in
# these runs draws nothing; the file does not depend on the drawing.

MOTION = "assembly.json"


def _motion(directory: Path) -> dict:
    return json.loads((directory / MOTION).read_text())


def test_an_accepted_split_writes_its_order_and_headings_beside_the_exports(monkeypatch, tmp_path):
    _run(FakeGen(), _sound(), monkeypatch, export_dir=tmp_path)
    assert _motion(tmp_path) == {
        "order": [0, 1],
        "releases": [[0.0, 0.0, 1.0], []],
        "stretch": [1, 1],
        "explode_fraction": 0.35,
    }


def test_a_verdict_that_did_not_pass_writes_no_motion(tmp_path):
    # Called directly for the reason the guide's own test of this is: no run
    # reaches the guide stage with a failed verdict today.
    Pipeline(generator=FakeGen())._guide(
        None,
        "a bracket",
        GOOD,
        {
            "ok": False,
            "order": [0, 1],
            "joints": [[1], [0]],
            "releases": [[0.0, 0.0, 1.0], []],
            "measured": True,
        },
        str(tmp_path),
        1,
    )
    assert not (tmp_path / MOTION).exists()


def test_a_single_part_build_writes_no_motion(monkeypatch, tmp_path):
    lone = AssemblyMeasurements(part_bboxes=[[10.0, 10.0, 10.0]], gaps=[], order=[0])
    _run(FakeGen(), lone, monkeypatch, export_dir=tmp_path)
    assert not (tmp_path / MOTION).exists()


def test_a_build_with_nowhere_to_export_to_writes_no_motion_and_says_nothing(
    monkeypatch, tmp_path, caplog
):
    # No export directory is an ordinary run -- an evaluation, a dry run -- and
    # not a failure to report on every one of them, nor a reason to write into
    # whatever directory the process happens to be in.
    monkeypatch.chdir(tmp_path)
    with caplog.at_level("WARNING", logger="cadless.pipeline"):
        result, _ = _run(FakeGen(), _sound(), monkeypatch)
    assert result.guide is not None
    assert list(tmp_path.iterdir()) == []
    assert caplog.records == []


def test_a_build_that_writes_no_motion_sweeps_the_last_one_s(monkeypatch, tmp_path):
    # Callers reuse one export directory across the builds of a turn, and the
    # file is taken up from whatever the directory holds when a build finishes.
    # Left in place, an earlier build's order would play over a later model.
    (tmp_path / MOTION).write_text('{"order": [1, 0], "releases": [[], []]}')
    lone = AssemblyMeasurements(part_bboxes=[[10.0, 10.0, 10.0]], gaps=[], order=[0])
    _run(FakeGen(), lone, monkeypatch, export_dir=tmp_path)
    assert not (tmp_path / MOTION).exists()


def test_a_writer_that_fails_costs_the_guide_and_keeps_the_motion(monkeypatch, tmp_path):
    # The motion is the measured order and headings, not anything said about
    # them, so losing the prose is no reason to lose the play.
    class _AngryWriter:
        def write(self, *a, **kw):
            raise RuntimeError("no provider")

    result, _ = _run(
        FakeGen(), _sound(), monkeypatch, export_dir=tmp_path, guide_writer=_AngryWriter()
    )
    assert result.guide is None
    assert _motion(tmp_path)["order"] == [0, 1]


def test_a_motion_that_cannot_be_written_costs_the_motion_and_not_the_guide(monkeypatch, tmp_path):
    # A full disk fails part-way through a write. What is left would be filed
    # against the version as its motion, and read back as broken JSON.
    real = Path.write_text

    def full_disk(self, data, *args, **kwargs):
        if self.name == MOTION:
            real(self, data[: len(data) // 2], *args, **kwargs)
            raise OSError("no space left on device")
        return real(self, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", full_disk)
    result, _ = _run(FakeGen(), _sound(), monkeypatch, export_dir=tmp_path)
    monkeypatch.undo()
    assert result.guide is not None
    assert not (tmp_path / MOTION).exists()


def test_a_heading_that_is_not_a_number_writes_no_motion(tmp_path):
    # Python writes an infinite float as ``Infinity``, which is not JSON: the
    # browser's parser would throw on the whole file. Refused here, it costs the
    # motion the way a failed write does, rather than filing a file nothing reads.
    Pipeline._write_motion(tmp_path, [1, 0], [[float("inf"), 0.0, 0.0], []])
    assert not (tmp_path / MOTION).exists()


def test_a_heading_that_is_nan_is_written_as_a_part_that_stays_put(tmp_path):
    # NaN is no direction, so it is not refused like an infinity: its part is
    # filed the way the one left standing is, and the rest of the motion stands.
    Pipeline._write_motion(tmp_path, [1, 0], [[float("nan"), 0.0, 0.0], [0.0, 0.0, 1.0]])
    assert _motion(tmp_path)["releases"] == [[], [0.0, 0.0, 1.0]]


def _write_box_stl(path: Path, lo, hi) -> None:
    """An axis-aligned box as a binary STL, the form the exporter writes."""
    import struct

    corners = [[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]
    quads = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    tris = [tri for a, b, c, d in quads for tri in ((a, b, c), (a, c, d))]
    body = b"".join(
        struct.pack("<12fH", 0.0, 0.0, 0.0, *(v for i in tri for v in corners[i]), 0)
        for tri in tris
    )
    path.write_bytes(b"\0" * 80 + struct.pack("<I", len(tris)) + body)


def test_the_motion_carries_how_far_each_exported_part_travels(tmp_path):
    # A pin through a block, exported as a build exports its parts: the motion
    # measures the pin's travel from those files, far enough to clear the block.
    _write_box_stl(tmp_path / "model_p0.stl", (-20, -20, 0), (20, 20, 10))
    _write_box_stl(tmp_path / "model_p1.stl", (-2, -2, -30), (2, 2, 60))
    Pipeline._write_motion(tmp_path, [0, 1], [[], [0.0, 0.0, 1.0]])
    travel = _motion(tmp_path)["travel"]
    assert travel[0] == 0.0
    assert -30 + travel[1] * 90 > 10


def test_parts_that_cannot_be_read_cost_the_travel_and_not_the_motion(tmp_path):
    # The viewport still has the order and headings to play, spaced as before.
    (tmp_path / "model_p0.stl").write_bytes(b"not a mesh")
    (tmp_path / "model_p1.stl").write_bytes(b"not a mesh")
    Pipeline._write_motion(tmp_path, [0, 1], [[], [0.0, 0.0, 1.0]])
    motion = _motion(tmp_path)
    assert motion["order"] == [0, 1]
    assert "travel" not in motion


def test_a_part_that_reads_back_empty_still_leaves_the_motion_written(tmp_path):
    # A well-formed binary STL with no facets: readable, with nothing in it. It
    # is in no one's way, and the motion that measures around it is written.
    _write_box_stl(tmp_path / "model_p0.stl", (-20, -20, 0), (20, 20, 10))
    (tmp_path / "model_p1.stl").write_bytes(b"\0" * 80 + (0).to_bytes(4, "little"))
    Pipeline._write_motion(tmp_path, [0, 1], [[], [0.0, 0.0, 1.0]])
    motion = _motion(tmp_path)
    assert motion["order"] == [0, 1]
    assert motion["travel"] == pytest.approx([0.0, 0.35])


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
TOO_BIG = _TWO_PARTS.format(a="Box(400, 20, 10)", b="Pos(210.2, 0, 0) * Box(20, 20, 10)")


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
def test_the_same_split_is_handed_over_with_its_verdict_when_the_turn_did_not_ask():
    # Measured all the same, and the shared volume found; but nobody asked for an
    # assembly, so the last attempt is handed over rather than refused.
    result = Pipeline(generator=FakeGen(OVERLAPPING), config=Settings(repair_max_attempts=1)).run(
        "two blocks", assembly=None
    )
    assert result.ok, result.error
    assert result.assembly["ok"] is False
    assert any("share" in f for f in result.assembly["failures"])


@pytest.mark.build123d
def test_a_part_too_big_for_the_bed_passes_when_the_turn_did_not_ask():
    result = Pipeline(generator=FakeGen(TOO_BIG), config=Settings(repair_max_attempts=1)).run(
        "a long bar beside a block", assembly=None
    )
    assert result.ok, result.error
    assert result.assembly["ok"] is True


# --- what the render reviewer is shown ------------------------------------


class _RecordingCritic:
    """Agrees with everything, and keeps whatever it was asked to look at."""

    def __init__(self):
        self.subjects: list = []

    def critique(self, intent, mesh_path):
        self.subjects.append(mesh_path)
        return Critique(True, "")


def _names_shown(subject) -> list[str]:
    """The filenames one critique was given, however many it was handed."""
    paths = [subject] if isinstance(subject, str) else list(subject)
    return sorted(Path(p).name for p in paths)


@pytest.mark.build123d
def test_the_reviewer_is_shown_the_whole_assembly_rather_than_one_part(tmp_path):
    """The one check asking "is this the shape requested" has to see all of it.

    Handed the first part alone, the reviewer answers about a fragment while the
    verdict is filed against the model -- so a wrong split can pass, and a sound
    one can be repaired against a mismatch nobody built.
    """
    critic = _RecordingCritic()
    result = Pipeline(
        generator=FakeGen(CLEARED),
        config=Settings(vlm_critique_enabled=True, repair_max_attempts=1),
        critic=critic,
    ).run("a two-part bracket", export_dir=str(tmp_path), assembly=SPEC)

    assert result.ok, result.error
    written = sorted(p.name for p in tmp_path.glob("model_p*.stl"))
    assert written == ["model_p0.stl", "model_p1.stl"], written
    assert critic.subjects, "the reviewer never ran"
    assert _names_shown(critic.subjects[0]) == written, (
        f"shown {_names_shown(critic.subjects[0])} of a {len(written)}-part build"
    )


@pytest.mark.build123d
def test_a_multi_solid_build_is_reviewed_whole_with_the_option_off_too(tmp_path):
    """The defect was never gated on the assembly option, so neither is the fix.

    Exporting splits on the solid count alone, so a turn that never asked for an
    assembly still writes parts and still had its review filed against the first
    of them.
    """
    critic = _RecordingCritic()
    result = Pipeline(
        generator=FakeGen(CLEARED),
        config=Settings(vlm_critique_enabled=True, repair_max_attempts=1),
        critic=critic,
    ).run("two blocks", export_dir=str(tmp_path), assembly=None)

    assert result.ok, result.error
    assert critic.subjects, "the reviewer never ran"
    assert _names_shown(critic.subjects[0]) == ["model_p0.stl", "model_p1.stl"]


def test_a_build_with_nothing_to_widen_to_hands_back_the_one_file(tmp_path):
    """One part, or a directory that names none, is reviewed as it always was.

    The second half is what keeps a caller holding a path to nothing working --
    raising there would take out a review that manages perfectly well with the
    one file it was handed.
    """
    single = tmp_path / "model.stl"
    single.write_bytes(b"x")
    assert critique_subject(str(single)) == str(single)

    bare = tmp_path / "nowhere"
    bare.mkdir()
    missing = bare / "model_p0.stl"
    assert critique_subject(str(missing)) == str(missing)


# --- the parts by the names their build gave them ---------------------------


def _named_exec(measurements, names):
    return ExecResult(
        ok=True,
        volume=1000.0,
        bbox=(10.0, 10.0, 10.0),
        part_count=len(names),
        stl_path="/tmp/model.stl",
        assembly=measurements,
        parts=[{"index": i, "name": n, "path": [], "color": None} for i, n in enumerate(names)],
    )


def test_the_check_names_the_parts_its_build_named(monkeypatch):
    monkeypatch.setattr(
        "cadless.pipeline.run_code",
        lambda code, **kw: _named_exec(_overlapping(), ["chassis", "wheel_fl"]),
    )
    result = Pipeline(generator=FakeGen(), config=Settings(repair_max_attempts=1)).run(
        "a car", assembly=SPEC
    )
    assert "chassis and wheel_fl share" in result.error


def test_the_guide_names_the_parts_its_build_named(monkeypatch):
    monkeypatch.setattr(
        "cadless.pipeline.run_code",
        lambda code, **kw: _named_exec(_sound(), ["axle_f", "wheel_fl"]),
    )
    result = Pipeline(generator=FakeGen(), config=Settings(repair_max_attempts=1)).run("a car")
    assert result.guide["parts"] == ["axle_f", "wheel_fl"]
    assert result.guide["steps"] == ["Start with axle_f.", "Fit wheel_fl to axle_f."]


def test_a_named_build_on_a_turn_that_asked_does_not_call_the_writer_s_model(monkeypatch):
    from cadless.guide_writer import GuideWriter

    provider = FakeChatProvider()
    monkeypatch.setattr(
        "cadless.pipeline.run_code",
        lambda code, **kw: _named_exec(_sound(), ["axle_f", "wheel_fl"]),
    )
    result = Pipeline(
        generator=FakeGen(),
        config=Settings(repair_max_attempts=1),
        guide_writer=GuideWriter(provider=provider),
    ).run("a car", assembly=SPEC)
    assert provider.calls == []
    assert result.guide["steps"] == ["Start with axle_f.", "Fit wheel_fl to axle_f."]


_TOY_CAR = """from build123d import *
chassis = Box(40, 20, 4)
chassis.label = "chassis"
axle = Pos(15, 0, 4) * Box(4, 24, 4)
axle.label = "axle_f"
wheel = Pos(15, 13, 4) * Box(6, 2, 6)
wheel.label = "wheel_fl"
result = Compound(children=[chassis, axle, wheel])
"""


@pytest.mark.build123d
def test_the_guide_for_a_labelled_toy_car_names_its_parts_end_to_end():
    result = Pipeline(generator=FakeGen(_TOY_CAR), config=Settings(repair_max_attempts=1)).run(
        "a toy car"
    )
    assert result.ok, result.error
    assert result.guide is not None, result.assembly
    assert sorted(result.guide["parts"]) == ["axle_f", "chassis", "wheel_fl"]
    assert not any("part " in step for step in result.guide["steps"])
    assert any("wheel_fl" in step and "axle_f" in step for step in result.guide["steps"])


def test_a_part_list_of_another_length_is_not_read_as_these_parts_names(monkeypatch):
    """A list that does not line up with the measured parts is not read."""
    monkeypatch.setattr(
        "cadless.pipeline.run_code",
        lambda code, **kw: _named_exec(_overlapping(), ["chassis", "wheel_fl", "spare"]),
    )
    result = Pipeline(generator=FakeGen(), config=Settings(repair_max_attempts=1)).run(
        "a car", assembly=SPEC
    )
    assert "part 1 and part 2 share" in result.error


def test_a_part_list_out_of_order_is_not_read_as_these_parts_names(monkeypatch):
    def out_of_order(code, **kw):
        res = _named_exec(_overlapping(), ["chassis", "wheel_fl"])
        res.parts.reverse()
        return res

    monkeypatch.setattr("cadless.pipeline.run_code", out_of_order)
    result = Pipeline(generator=FakeGen(), config=Settings(repair_max_attempts=1)).run(
        "a car", assembly=SPEC
    )
    assert "part 1 and part 2 share" in result.error
