"""End-to-end pipeline evaluation.

Runs the full generate->validate->execute->repair pipeline over a benchmark set
and reports success rate, first-try rate, **repair lift** (extra successes the
repair loop bought), average attempts, and any degenerate solids.

With ``forge_n`` above 1 each prompt is a best-of-N race instead of a single
generation, which is what makes forge measurable at all. The reported metrics stay
**winner-based** so they remain comparable with a single-run baseline: a race that
produces one usable model is one success, exactly as a single run would be. What
the race additionally cost is reported beside them rather than folded into them —
``candidate_attempts`` is the honest denominator for "did this buy anything", since
a lift bought at N times the spend is not the same result as the same lift for free.
"""

from __future__ import annotations

import csv
import io
import json
from collections import Counter
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
    # Race-only fields; None/0 on the single-run path so its rows are unchanged.
    rung: str | None = None  # which judge rung decided, when a race decided it
    candidates: int = 0  # how many candidates the race actually produced
    candidate_attempts: int = 0  # generation attempts summed across the whole field


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

    @property
    def candidate_attempts(self) -> int:
        """Generation attempts across every candidate — what the run actually spent.

        On the single-run path this equals the sum of ``attempts``. In a race it is
        the whole field's cost, which is the number a lift has to be weighed against.
        """
        return sum(r.candidate_attempts or r.attempts for r in self.records)

    @property
    def rung_distribution(self) -> dict[str, int]:
        """How many selections each judge rung decided.

        Empty on the single-run path, since nothing was selected. In a race it is
        the direct read-out of which rungs are alive: a distribution that is entirely
        ``filter`` means the ladder never got past its cheapest rung and the race
        chose by input order rather than on merit.
        """
        return dict(Counter(r.rung for r in self.records if r.rung))

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
            "candidate_attempts": self.candidate_attempts,
            "rung_distribution": self.rung_distribution,
            "records": [asdict(r) for r in self.records],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    def to_csv(self) -> str:
        # The race columns are APPENDED, never inserted. A recorded baseline is
        # read positionally as often as by name — `awk -F, '{print $6}'`, a
        # spreadsheet column, a plotting script — so moving `error` rightwards
        # would silently turn every row's empty `rung` into its error text.
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["id", "ok", "attempts", "repaired", "volume", "error", "rung", "candidates"])
        for r in self.records:
            w.writerow(
                [
                    r.id,
                    r.ok,
                    r.attempts,
                    r.repaired,
                    r.volume,
                    (r.error or "").replace("\n", " "),
                    r.rung or "",
                    r.candidates,
                ]
            )
        return buf.getvalue()


def run_pipeline_eval(
    prompts: list[BenchmarkPrompt] | None = None,
    pipeline: Pipeline | None = None,
    export_dir: str | None = None,
    forge_n: int = 1,
    provider=None,
) -> PipelineEvalReport:
    """Run every prompt and report the aggregate.

    ``forge_n`` at its default of 1 takes the single-run path and every
    pre-existing metric and record field is unchanged, which is what keeps a
    previously recorded baseline comparable. The **schema** is not unchanged: the
    report gains race fields, empty on this path, and the CSV gains two trailing
    columns. They are appended rather than inserted so existing column positions
    hold. Above 1 each prompt races that many candidates through the same
    primitive the live agent turn uses, so the A/B measures the shipped forge
    rather than a copy of it.

    ``provider`` is the judge's cheap-LLM rung. Leaving it out while racing does not
    fail; it silently removes the only rung that can break a tie once nothing else is
    injected, which the reported rung distribution will show as an all-``filter`` run.
    """
    prompts = prompts if prompts is not None else load_benchmark()
    # `is None`, not truthiness: an injected double that defines __bool__ or
    # __len__ falsily would otherwise fall through and construct the real
    # pipeline, which bills per prompt.
    if pipeline is None:
        pipeline = Pipeline()
    records: list[PipelineEvalRecord] = []
    for bp in prompts:
        if forge_n > 1:
            records.append(_race_record(bp, pipeline, export_dir, forge_n, provider))
        else:
            records.append(_single_record(bp, pipeline, export_dir))
    return PipelineEvalReport(records=records)


def _single_record(
    bp: BenchmarkPrompt, pipeline: Pipeline, export_dir: str | None
) -> PipelineEvalRecord:
    res = pipeline.run(bp.prompt, export_dir=export_dir)
    return _record(bp.id, res)


def _race_record(
    bp: BenchmarkPrompt,
    pipeline: Pipeline,
    export_dir: str | None,
    forge_n: int,
    provider,
) -> PipelineEvalRecord:
    # Imported here rather than at module scope so that reading a report never
    # depends on the racing machinery being importable.
    from cadless.forge import race_and_judge

    judged, candidates = race_and_judge(
        pipeline, bp.prompt, n=forge_n, provider=provider, export_dir=export_dir
    )
    win = judged.winner
    if win is None:
        # An empty field: no candidate to summarise, so record the failure rather
        # than inventing a winner. `ok` stays False and the race's cost is 0.
        return PipelineEvalRecord(
            id=bp.id, ok=False, attempts=0, error="forge: no candidates", candidates=0
        )
    # `no_winner` means nothing validated and executed, so the surfaced result is
    # the least-bad rather than a pass — score it as the failure it is.
    return _record(
        bp.id,
        win,
        ok=win.ok and not judged.no_winner,
        # A rung that only narrowed the field did not choose the winner; input
        # order did. Recording the narrowing rung here would inflate the
        # distribution with selections no rung actually made, which is the exact
        # question the distribution exists to answer.
        rung=judged.rung.value if judged.decided else "input-order",
        candidates=len(candidates),
        candidate_attempts=sum(_billable_attempts(c) for c in candidates),
    )


def _billable_attempts(c) -> int:
    """Attempts to charge a candidate with, floored at 1 once it has an error.

    A candidate whose generation raised is surfaced with an empty ``attempts``
    list, so counting the list alone bills it zero — under-reporting precisely the
    failures a race produces most, in the field whose job is to say what the race
    cost.
    """
    return max(1, c.attempt_count) if c.error else c.attempt_count


def _record(
    prompt_id: str,
    res,
    *,
    ok: bool | None = None,
    rung: str | None = None,
    candidates: int = 0,
    candidate_attempts: int = 0,
) -> PipelineEvalRecord:
    ok = res.ok if ok is None else ok
    return PipelineEvalRecord(
        id=prompt_id,
        ok=ok,
        attempts=res.attempt_count,
        volume=res.volume,
        repaired=ok and res.attempt_count > 1,
        degenerate=bool(ok and (res.volume is None or res.volume <= 0)),
        error=res.error,
        rung=rung,
        candidates=candidates,
        candidate_attempts=candidate_attempts,
    )
