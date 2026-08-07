"""Pipeline-eval tests."""

import json

import pytest

from cadless.evalkit.harness import _BENCHMARK_PATH, BenchmarkPrompt
from cadless.evalkit.pipeline_eval import (
    PipelineEvalRecord,
    PipelineEvalReport,
    run_pipeline_eval,
)
from cadless.pipeline import Pipeline

GOOD = "from build123d import *\nresult = Box(10, 10, 10)\n"


class AlwaysGood:
    def generate(self, intent, grounding=None, temperature=None, on_token=None):
        return GOOD

    def repair(self, intent, code, error, context=None):  # pragma: no cover - not reached
        return GOOD


def test_report_math_with_synthetic_records():
    report = PipelineEvalReport(
        records=[
            PipelineEvalRecord("a", ok=True, attempts=1),
            PipelineEvalRecord("b", ok=True, attempts=2, repaired=True),
            PipelineEvalRecord("c", ok=False, attempts=3, error="boom"),
            PipelineEvalRecord("d", ok=True, attempts=1, volume=0.0, degenerate=True),
        ]
    )
    assert report.success_rate == 0.75
    assert report.first_try_rate == 0.5  # a + d
    assert report.repair_lift == 0.25  # b
    assert report.avg_attempts == (1 + 2 + 3 + 1) / 4
    assert report.degenerate_count == 1


def test_report_serialises():
    report = PipelineEvalReport(records=[PipelineEvalRecord("a", ok=True, attempts=1)])
    parsed = json.loads(report.to_json())
    assert parsed["success_rate"] == 1.0
    assert report.to_csv().splitlines()[0] == "id,ok,attempts,repaired,volume,error"


@pytest.mark.build123d
def test_run_pipeline_eval_happy_path(tmp_path):
    prompts = [BenchmarkPrompt("p1", "x"), BenchmarkPrompt("p2", "y")]
    pipe = Pipeline(generator=AlwaysGood())
    report = run_pipeline_eval(prompts, pipeline=pipe, export_dir=str(tmp_path))
    assert report.total == 2
    assert report.success_rate == 1.0
    assert report.first_try_rate == 1.0
    assert report.repair_lift == 0.0
    assert report.degenerate_count == 0


@pytest.mark.bedrock
@pytest.mark.skipif(
    not _BENCHMARK_PATH.exists(),
    reason="benchmark prompts.jsonl is server-side data, not bundled",
)
def test_live_pipeline_eval_small():
    import os

    from cadless.evalkit.harness import load_benchmark

    os.environ.setdefault("AWS_REGION", "us-east-1")
    report = run_pipeline_eval(load_benchmark()[:4])
    assert report.total == 4
    assert report.success_rate >= 0.75  # baseline expectation for simple parts
    assert report.degenerate_count == 0
