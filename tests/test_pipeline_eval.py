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
    def generate(
        self, intent, grounding=None, temperature=None, on_token=None, images=(), on_reading=None
    ):
        return GOOD

    def repair(
        self, intent, code, error, context=None, images=()
    ):  # pragma: no cover - not reached
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
    assert report.to_csv().splitlines()[0] == "id,ok,attempts,repaired,volume,error,rung,candidates"


def test_the_race_columns_are_appended_so_existing_positions_hold():
    """A recorded baseline is read positionally as well as by name, so the two new
    columns go after ``error`` rather than before it. Inserting them would move
    ``error`` two columns right and make every row's empty rung read as its error
    text — a run with real failures would look clean."""
    report = PipelineEvalReport(
        records=[PipelineEvalRecord("a", ok=False, attempts=1, error="boom")]
    )

    header, row = report.to_csv().splitlines()[:2]
    assert header.split(",")[:6] == ["id", "ok", "attempts", "repaired", "volume", "error"]
    assert row.split(",")[5] == "boom"  # still column 5, where a baseline reader expects it


def test_a_single_run_report_carries_no_race_fields():
    """The racing fields exist on every report but stay empty without a race, so
    every pre-existing metric and record field of a single-run report is
    unchanged — the schema is additive, not identical."""
    report = PipelineEvalReport(records=[PipelineEvalRecord("a", ok=True, attempts=2)])

    parsed = json.loads(report.to_json())
    assert parsed["rung_distribution"] == {}
    assert parsed["candidate_attempts"] == 2  # falls back to the run's own attempts
    assert parsed["records"][0]["rung"] is None


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
