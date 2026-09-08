"""Racing inside the evaluation harness.

The harness could not reach forge at all before this: it ran one generation per
prompt and never touched the judge, so "forge on versus off" was not a measurement
anyone could take. These tests drive the racing path with a stub pipeline and a stub
provider — no network, no OCCT, nothing billed.

Two properties matter more than the rest and are asserted directly. ``forge_n=1``
must stay identical to the pre-existing single-run path, because a recorded baseline
is only comparable against a ruler that did not move; and the reported metrics must
stay winner-based, so a race that yields one usable model counts as one success
rather than as N attempts.
"""

from __future__ import annotations

from cadless.evalkit.harness import BenchmarkPrompt
from cadless.evalkit.pipeline_eval import run_pipeline_eval
from cadless.pipeline import Attempt, GenerationResult


def _res(code, *, ok=True, volume=1.0, attempts=1, error=None) -> GenerationResult:
    return GenerationResult(
        ok=ok,
        intent=code,
        code=code,
        volume=volume,
        error=error,
        attempts=[
            Attempt(n=i + 1, code=code, stage="execute", error=None) for i in range(attempts)
        ],
    )


class StubPipeline:
    """Serves scripted results, recording which path the harness took."""

    def __init__(self, single=None, field=None) -> None:
        self._single = single or _res("single")
        self._field = field or []
        self.run_calls = 0
        self.race_calls: list[int | None] = []

    def run(self, intent, export_dir=None, **kw):
        self.run_calls += 1
        return self._single

    def run_candidates(self, intent, n=None, export_dir=None, **kw):
        self.race_calls.append(n)
        return self._field


class ScoringProvider:
    def __init__(self, scores: dict[str, int]) -> None:
        self._scores = scores
        self.calls = 0

    def complete(self, *, model, system, user, temperature=None) -> str:
        self.calls += 1
        for fragment, score in self._scores.items():
            if fragment in user:
                return str(score)
        return "0"


def _prompts(n=1):
    return [BenchmarkPrompt(id=f"p{i}", prompt=f"prompt {i}") for i in range(n)]


def test_forge_n_one_is_the_untouched_single_run_path():
    """The default must not move the ruler a recorded baseline was measured with."""
    pipe = StubPipeline(single=_res("single", volume=8.0))

    report = run_pipeline_eval(prompts=_prompts(2), pipeline=pipe)

    assert pipe.run_calls == 2
    assert pipe.race_calls == []
    assert report.success_rate == 1.0
    assert report.rung_distribution == {}
    assert [r.rung for r in report.records] == [None, None]
    assert [r.candidates for r in report.records] == [0, 0]


def test_racing_goes_through_run_candidates_and_reports_the_deciding_rung():
    first = _res("result = Box(1,1,1)", volume=1.0)
    better = _res("result = Box(2,2,2)", volume=8.0)
    pipe = StubPipeline(field=[first, better])
    provider = ScoringProvider({"Box(1,1,1)": 1, "Box(2,2,2)": 9})

    report = run_pipeline_eval(prompts=_prompts(1), pipeline=pipe, forge_n=2, provider=provider)

    assert pipe.run_calls == 0
    assert pipe.race_calls == [2]
    assert report.rung_distribution == {"llm": 1}
    assert report.records[0].volume == 8.0  # the judged winner, not the first


def test_a_race_with_no_provider_reports_that_nothing_decided():
    """The degraded read-out this field exists to make visible.

    Nothing injected means no rung can settle a tie, so the winner is whichever
    candidate came back first. That is reported as ``input-order`` rather than as
    the name of a rung, because naming a rung here is what would let a race that
    merely paid N times pass for one that chose on merit.
    """
    pipe = StubPipeline(field=[_res("a"), _res("b")])

    report = run_pipeline_eval(prompts=_prompts(3), pipeline=pipe, forge_n=2)

    assert report.rung_distribution == {"input-order": 3}


def test_metrics_stay_winner_based_while_cost_is_reported_separately():
    """One usable model is one success; the field's cost sits beside it, not inside."""
    winner = _res("result = Box(2,2,2)", volume=8.0, attempts=1)
    loser = _res("bad", ok=False, volume=None, attempts=3, error="boom")
    pipe = StubPipeline(field=[winner, loser])

    report = run_pipeline_eval(prompts=_prompts(1), pipeline=pipe, forge_n=2)

    assert report.success_rate == 1.0
    assert report.first_try_rate == 1.0
    assert report.avg_attempts == 1.0  # the winner's attempts, not the field's
    assert report.candidate_attempts == 4  # 1 + 3, what the race actually spent
    assert report.records[0].candidates == 2


def test_a_race_nobody_wins_is_scored_as_a_failure():
    """``no_winner`` surfaces a least-bad result; it must not be counted as a pass."""
    pipe = StubPipeline(field=[_res("a", ok=False, attempts=2, error="boom")])

    report = run_pipeline_eval(prompts=_prompts(1), pipeline=pipe, forge_n=2)

    assert report.success_rate == 0.0
    assert report.records[0].ok is False
    assert report.records[0].degenerate is False


def test_an_empty_field_is_recorded_rather_than_crashing():
    """A fan-out that returned nothing is a failed prompt, not an exception."""
    pipe = StubPipeline(field=[])

    report = run_pipeline_eval(prompts=_prompts(1), pipeline=pipe, forge_n=2)

    assert report.success_rate == 0.0
    assert report.records[0].error == "forge: no candidates"
    assert report.records[0].candidates == 0


def test_the_report_serialises_the_race_fields():
    pipe = StubPipeline(field=[_res("a", volume=2.0), _res("b", volume=3.0)])

    report = run_pipeline_eval(prompts=_prompts(1), pipeline=pipe, forge_n=2)
    as_dict = report.to_dict()

    assert as_dict["rung_distribution"] == {"input-order": 1}
    assert as_dict["candidate_attempts"] == 2
    header = report.to_csv().splitlines()[0].split(",")
    # Appended, not inserted: the pre-existing columns keep their positions so a
    # baseline read positionally does not shift under a reader's feet.
    assert header[:6] == ["id", "ok", "attempts", "repaired", "volume", "error"]
    assert header[6:] == ["rung", "candidates"]


def test_a_candidate_that_raised_is_still_billed():
    """The fan-out surfaces a raised candidate with no attempts recorded. Counting
    the list alone would bill it zero, under-reporting exactly the failures a race
    produces most — in the field whose whole job is to say what the race cost."""
    winner = _res("ok", volume=1.0, attempts=1)
    raised = _res("boom", ok=False, volume=None, attempts=0, error="candidate 1 raised")
    pipe = StubPipeline(field=[winner, raised])

    report = run_pipeline_eval(prompts=_prompts(1), pipeline=pipe, forge_n=2)

    assert report.candidate_attempts == 2  # 1 for the winner, floored 1 for the raiser
