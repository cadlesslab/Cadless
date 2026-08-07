"""Evaluation harness for the text-to-CAD pipeline.

The harness is intentionally decoupled from any specific generator or executor:
it takes a ``generate_fn`` (prompt -> build123d code) and a ``run_fn`` (code ->
RunResult) so it can measure the bare model (the spike), a stub, or the
full repair-loop pipeline without change.

``run_pipeline_eval`` measures the other end of the range: the assembled
pipeline, repair loop and all. Both are exported here so callers never have to
reach into a submodule.
"""

from cadless.evalkit.harness import (
    BenchmarkPrompt,
    EvalRecord,
    EvalReport,
    available_tiers,
    default_run,
    load_benchmark,
    load_tier,
    run_eval,
)
from cadless.evalkit.pipeline_eval import (
    PipelineEvalRecord,
    PipelineEvalReport,
    run_pipeline_eval,
)

__all__ = [
    "BenchmarkPrompt",
    "EvalRecord",
    "EvalReport",
    "PipelineEvalRecord",
    "PipelineEvalReport",
    "available_tiers",
    "default_run",
    "load_benchmark",
    "load_tier",
    "run_eval",
    "run_pipeline_eval",
]
