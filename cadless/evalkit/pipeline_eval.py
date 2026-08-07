"""End-to-end pipeline evaluation.

Runs the full generate->validate->execute->repair pipeline over a benchmark set
and reports success rate, first-try rate, **repair lift** (extra successes the
repair loop bought), average attempts, and any degenerate solids.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import asdict, dataclass, field

from cadless.evalkit.harness import BenchmarkPrompt, load_benchmark
from cadless.pipeline import Pipeline


@dataclass
class PipelineEvalRecord:
    id: str
    ok: bool
    attempts: int
    volume: float | None = None
    repaired: bool = False  # succeeded only after >=1 repair
    degenerate: bool = False  # ok but non-positive volume (should never happen)
    error: str | None = None


@dataclass
class PipelineEvalReport:
    records: list[PipelineEvalRecord] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.records)

    @property
    def success_rate(self) -> float:
        return self._rate(r.ok for r in self.records)

    @property
    def first_try_rate(self) -> float:
        return self._rate(r.ok and not r.repaired for r in self.records)

    @property
    def repair_lift(self) -> float:
        """Fraction of prompts that succeeded *because of* the repair loop."""
        return self._rate(r.ok and r.repaired for r in self.records)

    @property
    def avg_attempts(self) -> float:
        return (sum(r.attempts for r in self.records) / self.total) if self.records else 0.0

    @property
    def degenerate_count(self) -> int:
        return sum(1 for r in self.records if r.degenerate)

    def _rate(self, flags) -> float:
        flags = list(flags)
        return (sum(1 for f in flags if f) / len(flags)) if flags else 0.0

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "success_rate": round(self.success_rate, 4),
            "first_try_rate": round(self.first_try_rate, 4),
            "repair_lift": round(self.repair_lift, 4),
            "avg_attempts": round(self.avg_attempts, 4),
            "degenerate_count": self.degenerate_count,
            "records": [asdict(r) for r in self.records],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    def to_csv(self) -> str:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["id", "ok", "attempts", "repaired", "volume", "error"])
        for r in self.records:
            w.writerow(
                [r.id, r.ok, r.attempts, r.repaired, r.volume, (r.error or "").replace("\n", " ")]
            )
        return buf.getvalue()


def run_pipeline_eval(
    prompts: list[BenchmarkPrompt] | None = None,
    pipeline: Pipeline | None = None,
    export_dir: str | None = None,
) -> PipelineEvalReport:
    prompts = prompts if prompts is not None else load_benchmark()
    # `is None`, not truthiness: an injected double that defines __bool__ or
    # __len__ falsily would otherwise fall through and construct the real
    # pipeline, which bills per prompt.
    if pipeline is None:
        pipeline = Pipeline()
    records: list[PipelineEvalRecord] = []
    for bp in prompts:
        res = pipeline.run(bp.prompt, export_dir=export_dir)
        records.append(
            PipelineEvalRecord(
                id=bp.id,
                ok=res.ok,
                attempts=res.attempt_count,
                volume=res.volume,
                repaired=res.ok and res.attempt_count > 1,
                degenerate=bool(res.ok and (res.volume is None or res.volume <= 0)),
                error=res.error,
            )
        )
    return PipelineEvalReport(records=records)
