"""Eval harness skeleton tests. No Bedrock; uses a stub generator."""

import json

import pytest

from cadless.evalkit import (
    EvalReport,
    PipelineEvalRecord,
    PipelineEvalReport,
    default_run,
    load_benchmark,
    run_eval,
    run_pipeline_eval,
)
from cadless.evalkit.harness import _BENCHMARK_PATH
from cadless.few_shot import FEW_SHOT

GOOD_CODE = "from build123d import *\nresult = Box(10, 10, 10)\n"
BAD_SYNTAX = "from build123d import *\nresult = Box(10, 10,\n"
NO_RESULT = "from build123d import *\nx = 1\n"


# The benchmark JSONL is server-side content, deliberately not bundled with
# the repo — anywhere it is absent these tests skip instead of failing.
needs_benchmark = pytest.mark.skipif(
    not _BENCHMARK_PATH.exists(),
    reason="benchmark prompts.jsonl is server-side data, not bundled",
)


@needs_benchmark
def test_benchmark_has_at_least_ten_prompts():
    prompts = load_benchmark()
    assert len(prompts) >= 10
    assert all(p.id and p.prompt for p in prompts)


@needs_benchmark
def test_run_eval_all_pass_with_good_generator():
    prompts = load_benchmark()[:5]
    report = run_eval(prompts, generate_fn=lambda _p: GOOD_CODE)
    assert report.total == 5
    assert report.compile_rate == 1.0
    assert report.success_rate == 1.0


@needs_benchmark
def test_run_eval_distinguishes_failure_modes():
    prompts = load_benchmark()[:3]
    codes = iter([GOOD_CODE, BAD_SYNTAX, NO_RESULT])
    report = run_eval(prompts, generate_fn=lambda _p: next(codes))
    assert report.compile_rate == 2 / 3  # bad-syntax one fails to compile
    assert report.success_rate == 1 / 3  # only the good one executes with `result`


@needs_benchmark
def test_report_is_serialisable_json_and_csv():
    report = run_eval(load_benchmark()[:2], generate_fn=lambda _p: GOOD_CODE)
    parsed = json.loads(report.to_json())
    assert parsed["total"] == 2
    assert "compile_rate" in parsed
    csv_text = report.to_csv()
    assert csv_text.splitlines()[0] == "id,compiled,executed,error"
    assert len(csv_text.splitlines()) == 3  # header + 2 rows


def test_default_run_executes_a_real_few_shot_snippet():
    # default_run actually compiles+execs; the few-shot Box example should pass.
    result = default_run(FEW_SHOT[0].code)
    assert result.ok
    assert result.error is None
    assert not default_run(NO_RESULT).ok
    assert not EvalReport(records=[]).records  # empty-report guard


def test_pipeline_eval_is_reachable_from_the_package_root():
    # evalkit's __init__ re-exports run_pipeline_eval as the package's public
    # entry point, and docs/extending/catalog.md imports it from there, so the
    # package path has to work — not only cadless.evalkit.pipeline_eval.
    assert callable(run_pipeline_eval)
    assert PipelineEvalReport().total == 0
    assert PipelineEvalRecord(id="box", ok=True, attempts=1).ok
