"""Best-of-N candidate selection — the judge ladder (C2).

C1 produces N fresh candidates via ``Pipeline.run_candidates``; this
module SELECTS the winner among them. The whole point is *cost discipline*: a
cheap-first, deterministic ladder where each rung only runs when the cheaper rungs
above it left a tie, so most selections cost nothing beyond running candidates we'd
run anyway.

The ladder, in priority order, short-circuiting upward::

  (a) HARD FILTER     drop candidates that did not validate + execute ok.
                      Exactly one survivor -> it wins, no lower rung runs.
                      No survivor -> mark ``no_winner`` and surface the
                      "least-bad" candidate (fewest repair attempts = it got
                      furthest before giving up) so callers always get *something*.
  (b) ASSERTIONS      among ok candidates, rank by Phase A geometry assertions: fewer failures is better. A unique best wins.
  (c) VLM CRITIQUE    only when a ``critic`` is enabled AND a tie remains: keep
                      the candidates the vision model says match the request.
  (d) CHEAP LLM JUDGE only when a tie still remains AND a ``provider`` is given:
                      score each remaining candidate against the spec via
                      ``provider.complete`` and keep the highest.

If a tie survives every rung (or the deciding dependency for a rung is absent), the
judge falls back to deterministic input order — it never crashes and always names a
winner among the ok candidates.

Scope (C2): selection only. Persisting losers as ``script_versions`` is C3; wiring into the live agent/chat path and the user toggle is C4
. This module is the pure, injectable primitive those will call: pass in
candidates plus the optional assertion mapper / critic / provider and tests drive
each rung deterministically.
"""

from __future__ import annotations

import enum
from collections.abc import Callable
from dataclasses import dataclass, field

from cadless.assertions import (
    GeometryAssertions,
    GeometrySignature,
    evaluate_assertions,
)
from cadless.config import Settings, settings
from cadless.model_profiles import resolve_model_id
from cadless.pipeline import GenerationResult

# Maps an ok candidate to the cheap geometry metrics measured at build time, so the
# judge can reuse Phase A ``evaluate_assertions``. Injected (the live caller builds
# it from each result's geometry; tests pass a canned one) — keeps the ladder pure.
SignatureOf = Callable[[GenerationResult], GeometrySignature]

# System prompt for the cheap LLM tie-breaker. It scores how well one candidate's
# code satisfies the request on a 0-10 scale; we parse the leading integer.
_JUDGE_SYSTEM = (
    "You are a strict CAD code reviewer. Given a build123d generation request and "
    "ONE candidate's code, rate how well the code satisfies the request on an "
    "integer scale from 0 (wrong) to 10 (perfect). Reply with ONLY that integer."
)


class Rung(enum.Enum):
    """Which ladder rung decided the winner (for inspection/telemetry)."""

    FILTER = "filter"
    ASSERTIONS = "assertions"
    VLM = "vlm"
    LLM = "llm"


@dataclass
class JudgeResult:
    """Outcome of the ladder: the winner plus how it was chosen.

    ``no_winner`` is True only when NO candidate validated+executed ok; in that
    case ``winner`` is the least-bad candidate (or ``None`` for an empty list) so
    callers can still act, clearly flagged as a non-passing selection.
    """

    winner: GenerationResult | None
    rung: Rung
    ranking: list[GenerationResult] = field(default_factory=list)
    no_winner: bool = False


def select_winner(
    candidates: list[GenerationResult],
    *,
    intent: str,
    assertions: GeometryAssertions | None = None,
    signature_of: SignatureOf | None = None,
    critic=None,
    provider=None,
    config: Settings | None = None,
) -> JudgeResult:
    """Pick the best candidate via the cheap-first ladder (see module docstring).

    Each rung runs ONLY when the cheaper rungs above it left more than one
    contender, so expensive rungs (VLM, LLM) cost nothing on the common case where
    the filter or assertions already decided it.
    """
    cfg = config or settings

    # --- Rung (a): HARD FILTER -------------------------------------------------
    ok = [c for c in candidates if c.ok]
    if not ok:
        # Nobody validated+executed. Surface the least-bad (got furthest = fewest
        # repair attempts) but flag it: this is not a passing selection.
        least_bad = _least_bad(candidates)
        ranking = sorted(candidates, key=_repair_attempts)
        return JudgeResult(winner=least_bad, rung=Rung.FILTER, ranking=ranking, no_winner=True)
    if len(ok) == 1:
        return JudgeResult(winner=ok[0], rung=Rung.FILTER, ranking=ok)

    # --- Rung (b): ASSERTIONS --------------------------------------------------
    contenders, rung = ok, Rung.FILTER
    if assertions is not None and not assertions.is_empty() and signature_of is not None:
        # Evaluate each candidate's assertions exactly once (cost discipline).
        fails = {id(c): _assertion_failures(c, assertions, signature_of) for c in contenders}
        scored = sorted(contenders, key=lambda c: fails[id(c)])
        best_score = fails[id(scored[0])]
        winners = [c for c in scored if fails[id(c)] == best_score]
        if len(winners) == 1:
            return JudgeResult(winner=winners[0], rung=Rung.ASSERTIONS, ranking=scored)
        contenders, rung = winners, Rung.ASSERTIONS

    # --- Rung (c): VLM CRITIQUE tie-breaker ------------------------------------
    if critic is not None and len(contenders) > 1:
        matched = [
            c for c in contenders if c.glb_path and critic.critique(intent, c.glb_path).matches
        ]
        if len(matched) == 1:
            return JudgeResult(winner=matched[0], rung=Rung.VLM, ranking=contenders)
        if matched:  # narrow to the matching subset; a tie still falls through
            contenders, rung = matched, Rung.VLM

    # --- Rung (d): CHEAP LLM JUDGE ---------------------------------------------
    if provider is not None and len(contenders) > 1:
        model_id = resolve_model_id(cfg.bedrock_fast_model_slug)
        scored = sorted(
            contenders,
            key=lambda c: -_llm_score(provider, model_id, intent, c),
        )
        return JudgeResult(winner=scored[0], rung=Rung.LLM, ranking=scored)

    # --- Fallback: deterministic input order -----------------------------------
    return JudgeResult(winner=contenders[0], rung=rung, ranking=contenders)


def _repair_attempts(c: GenerationResult) -> int:
    """How many tries the candidate burned; fewer == it got furthest fastest."""
    return c.attempt_count


def _least_bad(candidates: list[GenerationResult]) -> GenerationResult | None:
    """The failed candidate that got furthest (fewest repair attempts); None if empty."""
    if not candidates:
        return None
    return min(candidates, key=_repair_attempts)


def _assertion_failures(
    c: GenerationResult, assertions: GeometryAssertions, signature_of: SignatureOf
) -> int:
    """Number of failed Phase A assertions for a candidate (fewer is better)."""
    report = evaluate_assertions(signature_of(c), assertions)
    return len(report.failures)


def _llm_score(provider, model_id: str, intent: str, c: GenerationResult) -> int:
    """Cheap LLM score of a candidate against the spec; 0 on any parse/IO failure."""
    user = f"Request:\n{intent}\n\nCandidate code:\n{c.code or ''}"
    try:
        text = provider.complete(model=model_id, system=_JUDGE_SYSTEM, user=user)
    except Exception:
        return 0
    return _parse_score(text)


def _parse_score(text: str) -> int:
    """Parse the leading integer from the judge reply; 0 when none is present."""
    digits = ""
    for ch in text.strip():
        if ch.isdigit():
            digits += ch
        elif digits:
            break
    return int(digits) if digits else 0
