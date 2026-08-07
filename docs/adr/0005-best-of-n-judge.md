# ADR-0005: Best-of-N candidates judged by a cheap-first ladder

## Status

Accepted; off by default.

## Context

A single generation sometimes lands a plausible-but-wrong solid. Sampling
several candidates raises the ceiling, but only if picking the winner is
cheaper than generating the candidates — a judge that burns a frontier-model
call per candidate would erase the benefit.

## Decision

- `Pipeline.run_candidates(intent, n, ...)` produces N fresh candidates in
  parallel (thread pool — provider calls are IO-bound and execution is a
  subprocess, so they genuinely overlap).
- `cadless/judge.py:select_winner` picks the winner by climbing a
  **cheap-first ladder**, short-circuiting as soon as a rung is decisive
  (the `Rung` enum records which one decided, for inspection):
  1. **filter** — hard disqualifiers: failed builds, degenerate geometry.
  2. **assertions** — deterministic geometry post-conditions, when given.
  3. **vlm** — render critique, when enabled.
  4. **llm** — a cheap-model comparison as the last resort.
  Ties fall back to input order, keeping the outcome deterministic.
- Losing candidates are not discarded silently: when the feature is on,
  `cadless/forge.py` persists them alongside the winner for later
  inspection.
- The whole mechanism is **opt-in twice** — a per-request flag and a global
  setting must both be true — and N scales with the configured budget. The
  default experience stays single-candidate.

## Consequences

- Quality ceiling rises with compute the user explicitly chooses to spend;
  nobody pays for best-of-N by accident.
- Most winners are decided by the free rungs (filter/assertions); the paid
  rung only breaks real ties.
- Persisted losers give contributors a corpus for studying failure modes,
  and give the judge itself regression material.
