"""Judge ladder tests (C2).

The judge SELECTS one winning candidate from a best-of-N list via a cheap-first,
deterministic ladder: (a) hard filter -> (b) Phase A assertions -> (c) VLM
critique tie-breaker -> (d) cheap LLM judge. Each rung runs ONLY when the cheaper
rungs left a tie; spies prove that cost discipline. No real OCCT/Bedrock — canned
GenerationResults, an injected signature mapper, a stub critic, and the fake
provider drive every rung deterministically.
"""

from __future__ import annotations

from cadless.assertions import GeometryAssertions, GeometrySignature
from cadless.judge import Rung, select_winner
from cadless.pipeline import Attempt, GenerationResult


def _ok(intent="a bracket", code="result = Box(1,1,1)", **kw):
    return GenerationResult(ok=True, intent=intent, code=code, **kw)


def _bad(intent="a bracket", attempts=None, **kw):
    return GenerationResult(ok=False, intent=intent, attempts=attempts or [], **kw)


class _SpyCritic:
    """Stub VlmCritic: a canned matches verdict per glb_path, records calls."""

    def __init__(self, verdicts: dict[str, bool]):
        self._verdicts = verdicts
        self.calls: list[str] = []

    def critique(self, intent, glb_path):
        self.calls.append(glb_path)
        from cadless.vlm_critique import Critique

        return Critique(matches=self._verdicts.get(glb_path, False), feedback="x")


class _SpyProvider:
    """Fake provider scoring a candidate; records every complete() call."""

    def __init__(self, scores: dict[str, str]):
        # map a fingerprint substring -> score text the judge will parse
        self._scores = scores
        self.calls: list[dict] = []

    def complete(self, *, model, system, user, temperature=None):
        self.calls.append({"system": system, "user": user, "temperature": temperature})
        for key, score in self._scores.items():
            if key in user:
                return score
        return "0"


class _NeverProvider:
    def complete(self, *, model, system, user, temperature=None):
        raise AssertionError("LLM judge must not be called")


class _NeverCritic:
    def critique(self, intent, glb_path):
        raise AssertionError("critic must not be called")


# ---------------------------------------------------------------------------
# Rung (a): HARD FILTER
# ---------------------------------------------------------------------------


def test_hard_filter_drops_non_ok_and_lone_survivor_wins_without_lower_rungs():
    """A single ok candidate among failures wins on the filter rung alone, with
    NO assertion eval, NO critic call, NO LLM call."""
    winner = _ok(code="A")

    def _never_sig(_):
        raise AssertionError("assertions must not be evaluated for a lone survivor")

    result = select_winner(
        [_bad(), winner, _bad()],
        intent="a bracket",
        assertions=GeometryAssertions(expected_part_count=1),
        signature_of=_never_sig,
        critic=_NeverCritic(),
        provider=_NeverProvider(),
    )
    assert result.winner is winner
    assert result.rung is Rung.FILTER
    assert result.no_winner is False


def test_all_fail_returns_least_bad_marked_no_winner():
    """When NO candidate validated+executed, the judge marks no_winner but still
    returns the least-bad candidate (fewest repair attempts = got furthest)."""
    few = _bad(attempts=[Attempt(1, "x", "validate", "e")])
    many = _bad(attempts=[Attempt(i, "x", "validate", "e") for i in range(1, 4)])
    result = select_winner([many, few], intent="a bracket")
    assert result.no_winner is True
    assert result.winner is few  # fewest repair attempts
    assert result.rung is Rung.FILTER


def test_empty_candidate_list_is_no_winner():
    result = select_winner([], intent="a bracket")
    assert result.no_winner is True
    assert result.winner is None


# ---------------------------------------------------------------------------
# Rung (b): ASSERTIONS
# ---------------------------------------------------------------------------


def _sig(part_count):
    return GeometrySignature(volume=1.0, bbox=(1, 1, 1), part_count=part_count)


def test_assertions_break_tie_among_ok_candidates_without_critic_or_llm():
    """Two ok candidates -> assertion pass-rate picks the one with no failures;
    critic and LLM are never consulted."""
    good = _ok(code="GOOD")
    bad = _ok(code="BAD")
    sigs = {"GOOD": _sig(1), "BAD": _sig(5)}

    result = select_winner(
        [good, bad],
        intent="a bracket",
        assertions=GeometryAssertions(expected_part_count=1),
        signature_of=lambda c: sigs[c.code],
        critic=_NeverCritic(),
        provider=_NeverProvider(),
    )
    assert result.winner is good
    assert result.rung is Rung.ASSERTIONS


def test_assertions_skipped_when_no_assertions_given():
    """No assertion spec -> the assertions rung is a no-op tie, control falls
    through to the next available rung (here the LLM judge)."""
    a = _ok(code="A")
    b = _ok(code="B")
    provider = _SpyProvider({"A": "9", "B": "2"})
    result = select_winner(
        [a, b],
        intent="a bracket",
        assertions=None,
        provider=provider,
    )
    assert result.winner is a
    assert result.rung is Rung.LLM


# ---------------------------------------------------------------------------
# Rung (c): VLM CRITIQUE tie-breaker
# ---------------------------------------------------------------------------


def test_vlm_breaks_tie_only_when_critic_enabled_and_still_tied():
    """Two ok candidates with equal assertion results -> the critic breaks the
    tie. The LLM judge is never reached."""
    a = _ok(code="A", glb_path="/a.glb")
    b = _ok(code="B", glb_path="/b.glb")
    critic = _SpyCritic({"/a.glb": True, "/b.glb": False})
    result = select_winner(
        [a, b],
        intent="a bracket",
        critic=critic,
        provider=_NeverProvider(),
    )
    assert result.winner is a
    assert result.rung is Rung.VLM
    assert critic.calls == ["/a.glb", "/b.glb"]


def test_vlm_skipped_when_no_critic_falls_through_to_llm():
    a = _ok(code="A")
    b = _ok(code="B")
    provider = _SpyProvider({"A": "1", "B": "8"})
    result = select_winner([a, b], intent="a bracket", provider=provider)
    assert result.winner is b
    assert result.rung is Rung.LLM


# ---------------------------------------------------------------------------
# Rung (d): CHEAP LLM JUDGE
# ---------------------------------------------------------------------------


def test_llm_judge_only_runs_when_still_tied_after_vlm():
    """Critic matches BOTH (still tied) -> LLM judge breaks it. The critic ran
    (it's enabled) and then the LLM ran exactly once per remaining candidate."""
    a = _ok(code="A", glb_path="/a.glb")
    b = _ok(code="B", glb_path="/b.glb")
    critic = _SpyCritic({"/a.glb": True, "/b.glb": True})  # both match -> tie
    provider = _SpyProvider({"A": "3", "B": "7"})
    result = select_winner([a, b], intent="a bracket", critic=critic, provider=provider)
    assert result.winner is b
    assert result.rung is Rung.LLM
    assert len(critic.calls) == 2
    assert len(provider.calls) == 2  # one score per remaining candidate


def test_llm_judge_not_called_when_no_provider():
    """No provider and still tied after cheaper rungs -> the judge falls back to
    a deterministic order rather than crashing; no LLM call happens."""
    a = _ok(code="A")
    b = _ok(code="B")
    result = select_winner([a, b], intent="a bracket")  # no critic, no provider
    assert result.winner is a  # deterministic fallback: first candidate
    assert result.no_winner is False


def test_ranking_is_returned_for_inspection():
    winner = _ok(code="W")
    result = select_winner([_bad(), winner], intent="a bracket")
    assert result.winner is winner
    assert winner in result.ranking
    assert result.ranking[0] is winner  # winner ranked first
