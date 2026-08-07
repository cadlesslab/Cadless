"""Repair-loop pipeline tests.

Unit tests inject a scripted fake generator. Execution-bearing tests are marked
build123d; the full live test is marked bedrock.
"""

import os

import pytest

from cadless.config import Settings, settings
from cadless.pipeline import Pipeline, generate_cad

GOOD = "from build123d import *\nresult = Box(10, 10, 10)\n"
GOOD_PARAMS = (
    "from build123d import *\n"
    'params = {"size": 10}\n'
    'result = Box(params["size"], params["size"], params["size"])\n'
)
BANNED = "import os\nfrom build123d import *\nresult = Box(1, 1, 1)\n"
RUNTIME_FAIL = "from build123d import *\nresult = Box(1,1,1) - Box(1,1,1)\n"  # empty solid


class FakeGen:
    """Scripted generator: generate() returns outputs[0]; each repair() pops next."""

    def __init__(self, outputs):
        self._outputs = list(outputs)
        self.repairs = 0

    def generate(self, intent, grounding=None, temperature=None, on_token=None):
        self.last_grounding = grounding
        self.last_temperature = temperature
        out = self._outputs[0]
        if on_token is not None:  # simulate streaming: emit the code in two chunks
            mid = len(out) // 2
            for piece in (out[:mid], out[mid:]):
                if piece:
                    on_token(piece)
        return out

    def refine(self, intent, prior_code):
        self.refine_calls = getattr(self, "refine_calls", 0) + 1
        self.last_refine = (intent, prior_code)
        return self._outputs[0]

    def repair(self, intent, code, error, context=None):
        self.repairs += 1
        self.last_repair_context = context
        self.last_repair_error = error
        return self._outputs[self.repairs]


def test_exhausted_on_persistent_validation_failure():
    # All outputs fail validation -> no execution needed (no build123d).
    gen = FakeGen([BANNED, BANNED, BANNED])
    result = Pipeline(generator=gen, config=Settings(repair_max_attempts=3)).run("x")
    assert not result.ok
    assert result.attempt_count == 3
    assert all(a.stage == "validate" for a in result.attempts)
    assert "validation" in result.error


def test_repair_count_respects_budget():
    gen = FakeGen([BANNED, BANNED])
    Pipeline(generator=gen, config=Settings(repair_max_attempts=2)).run("x")
    assert gen.repairs == 1  # 2 tries total => exactly 1 repair


@pytest.mark.build123d
def test_success_first_try(tmp_path):
    gen = FakeGen([GOOD])
    result = Pipeline(generator=gen).run("a 10mm cube", export_dir=str(tmp_path))
    assert result.ok, result.error
    assert result.attempt_count == 1
    assert result.volume == pytest.approx(1000, rel=1e-3)
    assert os.path.getsize(result.step_path) > 0
    assert os.path.getsize(result.glb_path) > 0
    # all registered formats are produced by the worker
    assert os.path.getsize(result.stl_path) > 0
    assert os.path.getsize(result.obj_path) > 0


@pytest.mark.build123d
def test_validation_failure_then_repair_succeeds():
    gen = FakeGen([BANNED, GOOD])
    result = Pipeline(generator=gen, config=Settings(repair_max_attempts=3)).run("x")
    assert result.ok, result.error
    assert result.attempt_count == 2
    assert result.attempts[0].stage == "validate" and result.attempts[0].error
    assert result.attempts[1].stage == "execute" and result.attempts[1].error is None


@pytest.mark.build123d
def test_execution_failure_then_repair_succeeds():
    gen = FakeGen([RUNTIME_FAIL, GOOD])
    result = Pipeline(generator=gen, config=Settings(repair_max_attempts=3)).run("x")
    assert result.ok, result.error
    assert result.attempts[0].stage == "execute" and result.attempts[0].error


def test_refine_mode_calls_refine_not_generate():
    gen = FakeGen([BANNED])  # fails validation -> no execution needed
    result = Pipeline(generator=gen, config=Settings(repair_max_attempts=1)).run(
        "make it bigger", prior_code="from build123d import *\nresult = Box(5,5,5)"
    )
    assert not result.ok
    assert getattr(gen, "refine_calls", 0) == 1
    assert gen.last_refine == ("make it bigger", "from build123d import *\nresult = Box(5,5,5)")


def test_run_threads_grounding_into_fresh_generation():
    """Pipeline.run forwards grounding to the fresh-generation branch.

    The grounding block must reach CodeGenerator.generate (and thus the provider
    prompt) so dynamic RAG is actually live, not just a dormant seam.
    """
    gen = FakeGen([BANNED])  # fails validation -> no execution needed
    Pipeline(generator=gen, config=Settings(repair_max_attempts=1)).run(
        "a bracket", grounding="SOME GROUNDING"
    )
    assert gen.last_grounding == "SOME GROUNDING"


def test_grounding_reaches_provider_prompt():
    """end-to-end through the real CodeGenerator -> provider prompt.

    Asserts the grounding text appears in the user message the provider sees, via
    a fake provider spy on complete().
    """
    from cadless.prompts import CodeGenerator

    class SpyProvider:
        def __init__(self):
            self.last_user = None

        def complete(self, *, model, system, user, temperature=None):
            self.last_user = user
            return f"```python\n{BANNED}```"

    spy = SpyProvider()
    gen = CodeGenerator(provider=spy, model="fake-model")
    Pipeline(generator=gen, config=Settings(repair_max_attempts=1)).run(
        "a bracket", grounding="SOME GROUNDING"
    )
    assert "SOME GROUNDING" in spy.last_user


def test_run_without_grounding_is_unchanged():
    """default grounding=None keeps the legacy no-retrieval prompt."""
    gen = FakeGen([BANNED])
    Pipeline(generator=gen, config=Settings(repair_max_attempts=1)).run("a bracket")
    assert gen.last_grounding is None


@pytest.mark.build123d
def test_refine_success_produces_geometry(tmp_path):
    refined = (
        "from build123d import *\n"
        'params = {"size": 20}\n'
        'result = Box(params["size"], params["size"], params["size"])\n'
    )
    gen = FakeGen([refined])
    result = Pipeline(generator=gen).run(
        "make it 20mm",
        export_dir=str(tmp_path),
        prior_code="from build123d import *\nresult = Box(5,5,5)",
    )
    assert result.ok, result.error
    assert result.volume == pytest.approx(8000, rel=1e-3)
    assert result.parameters == {"size": 20}


@pytest.mark.build123d
def test_success_captures_parameters(tmp_path):
    gen = FakeGen([GOOD_PARAMS])
    result = Pipeline(generator=gen).run("a 10mm cube", export_dir=str(tmp_path))
    assert result.ok, result.error
    assert result.parameters == {"size": 10}


@pytest.mark.build123d
def test_success_without_params_block_yields_empty_dict(tmp_path):
    gen = FakeGen([GOOD])  # no params block
    result = Pipeline(generator=gen).run("a 10mm cube", export_dir=str(tmp_path))
    assert result.ok, result.error
    assert result.parameters == {}


def test_execution_failure_threads_repair_context(monkeypatch):
    """On execution failure the worker's RepairContext reaches generator.repair()."""
    from cadless import pipeline as pipeline_mod
    from cadless.worker import ExecResult, RepairContext

    ctx = RepairContext(
        error_type="StdFail_NotDone",
        message="BRep_API: command not done",
        offending_line="result = Cylinder(5, 10)",
        last_traceback="Traceback (most recent call last):\nStdFail_NotDone: ...",
    )

    def fake_run_code(code, *, export_dir=None, export_scale=1.0, config=None):
        return ExecResult(ok=False, error="boom", repair_context=ctx)

    monkeypatch.setattr(pipeline_mod, "run_code", fake_run_code)
    gen = FakeGen([GOOD, GOOD])  # GOOD passes validation, fake exec always fails
    Pipeline(generator=gen, config=Settings(repair_max_attempts=2)).run("x")
    assert gen.repairs == 1
    assert gen.last_repair_context is ctx


def _exec_ok(**kw):
    """Build a successful ExecResult with sane geometry defaults for assertions."""
    from cadless.worker import ExecResult

    base = dict(
        ok=True,
        volume=1000.0,
        bbox=(10.0, 10.0, 10.0),
        part_count=1,
        manifold=True,
        min_wall_thickness=2.0,
        step_path="s",
        glb_path="g",
        stl_path="t",
        obj_path="o",
    )
    base.update(kw)
    return ExecResult(**base)


def _pipeline_with_exec(monkeypatch, exec_result, outputs):
    from cadless import pipeline as pipeline_mod

    monkeypatch.setattr(
        pipeline_mod,
        "run_code",
        lambda code, *, export_dir=None, export_scale=1.0, config=None: exec_result,
    )
    gen = FakeGen(outputs)
    return gen


def test_run_forwards_export_scale_to_worker(monkeypatch):
    """Pipeline.run passes export_scale through to run_code (issue #30)."""
    from cadless import pipeline as pipeline_mod

    seen = {}

    def fake_run_code(code, *, export_dir=None, export_scale=1.0, config=None):
        seen["export_scale"] = export_scale
        return _exec_ok()

    monkeypatch.setattr(pipeline_mod, "run_code", fake_run_code)
    result = Pipeline(generator=FakeGen([GOOD])).run("x", export_scale=1000.0)
    assert result.ok
    assert seen["export_scale"] == 1000.0
    # default stays the identity scale (legacy callers unchanged)
    Pipeline(generator=FakeGen([GOOD])).run("x")
    assert seen["export_scale"] == 1.0


def test_generate_cad_forwards_export_scale(monkeypatch):
    """The convenience entry point threads export_scale into Pipeline.run (#30)."""
    from cadless import pipeline as pipeline_mod

    seen = {}

    class FakePipeline:
        def run(
            self,
            intent,
            export_dir=None,
            on_progress=None,
            prior_code=None,
            assertions=None,
            export_scale=1.0,
        ):
            seen["export_scale"] = export_scale
            return "res"

    monkeypatch.setattr(pipeline_mod, "Pipeline", lambda: FakePipeline())
    assert generate_cad("x", export_scale=1000.0) == "res"
    assert seen["export_scale"] == 1000.0


def test_passing_assertion_produces_no_repair_signal(monkeypatch):
    from cadless.assertions import GeometryAssertions

    gen = _pipeline_with_exec(monkeypatch, _exec_ok(bbox=(10.0, 10.0, 10.0)), [GOOD, GOOD])
    result = Pipeline(generator=gen, config=Settings(repair_max_attempts=3)).run(
        "x", assertions=GeometryAssertions(bbox=(10.0, 10.0, 10.0), bbox_tolerance=0.5)
    )
    assert result.ok
    assert gen.repairs == 0  # no repair signal raised
    assert result.attempt_count == 1


def test_failing_assertion_adds_repair_signal_not_hard_stop(monkeypatch):
    from cadless.assertions import GeometryAssertions

    # First build violates bbox; the (forced) repair yields a second GOOD build
    # that satisfies it -> the run still succeeds (no hard stop).
    results = iter([_exec_ok(bbox=(20.0, 10.0, 10.0)), _exec_ok(bbox=(10.0, 10.0, 10.0))])
    from cadless import pipeline as pipeline_mod

    monkeypatch.setattr(
        pipeline_mod,
        "run_code",
        lambda code, *, export_dir=None, export_scale=1.0, config=None: next(results),
    )
    gen = FakeGen([GOOD, GOOD])
    result = Pipeline(generator=gen, config=Settings(repair_max_attempts=3)).run(
        "x", assertions=GeometryAssertions(bbox=(10.0, 10.0, 10.0), bbox_tolerance=0.5)
    )
    assert result.ok  # not a hard stop
    assert gen.repairs == 1  # the failed assertion produced a repair signal
    # the repair prompt carried the assertion failure
    assert "bounding box" in str(gen.last_repair_error).lower()


def test_failing_assertion_at_budget_end_does_not_hard_stop(monkeypatch):
    """A failed assertion on the last attempt returns the build, never raises."""
    from cadless.assertions import GeometryAssertions

    gen = _pipeline_with_exec(monkeypatch, _exec_ok(part_count=5), [GOOD])
    result = Pipeline(generator=gen, config=Settings(repair_max_attempts=1)).run(
        "x", assertions=GeometryAssertions(expected_part_count=1)
    )
    # budget exhausted, but the build itself was valid -> returned, not a failure.
    assert result.ok
    assert gen.repairs == 0


def test_missing_assertions_never_blocks(monkeypatch):
    gen = _pipeline_with_exec(monkeypatch, _exec_ok(), [GOOD])
    result = Pipeline(generator=gen).run("x")  # no assertions arg
    assert result.ok
    assert gen.repairs == 0


@pytest.mark.build123d
def test_assertion_drives_repair_on_real_build(tmp_path):
    """End-to-end (real OCCT): a bbox assertion the first build fails, the repair
    fixes -> the run succeeds via the assertion repair signal."""
    from cadless.assertions import GeometryAssertions

    small = "from build123d import *\nresult = Box(10, 10, 10)\n"
    big = "from build123d import *\nresult = Box(20, 20, 20)\n"
    gen = FakeGen([small, big])
    result = Pipeline(generator=gen, config=Settings(repair_max_attempts=3)).run(
        "a 20mm cube",
        export_dir=str(tmp_path),
        assertions=GeometryAssertions(bbox=(20.0, 20.0, 20.0), bbox_tolerance=0.5),
    )
    assert result.ok, result.error
    assert gen.repairs == 1
    assert result.bbox == pytest.approx((20, 20, 20), rel=1e-3)


@pytest.mark.bedrock
def test_live_end_to_end(tmp_path):
    os.environ.setdefault("AWS_REGION", "us-east-1")
    result = generate_cad(
        "A rectangular plate 50x30x5 mm with a 10 mm hole through the centre.",
        export_dir=str(tmp_path),
    )
    assert result.ok, result.error
    assert result.volume and result.volume > 0
    assert os.path.getsize(result.step_path) > 0
    assert os.path.getsize(result.glb_path) > 0


def test_pipeline_snapshots_config_at_construction():
    """A pipeline answers to the settings it was built with, not to later edits.

    The settings layer applies a change by mutating the shared singleton in
    place, so a pipeline holding that object would pick up an edit part-way
    through a turn. That leaves the turn attributable to no single
    configuration, which is exactly what an A/B of a quality knob needs it to
    be. `build_pipeline()` runs per request, so each request still sees the
    current values.
    """
    before = settings.repair_max_attempts
    pipeline = Pipeline(generator=FakeGen([GOOD]))
    try:
        settings.repair_max_attempts = before + 5
        assert pipeline._cfg.repair_max_attempts == before
    finally:
        settings.repair_max_attempts = before


def test_explicit_config_is_also_snapshotted():
    """An injected Settings is copied too — the caller keeps their own object."""
    cfg = Settings(repair_max_attempts=2)
    pipeline = Pipeline(generator=FakeGen([GOOD]), config=cfg)

    cfg.repair_max_attempts = 7

    assert pipeline._cfg.repair_max_attempts == 2
