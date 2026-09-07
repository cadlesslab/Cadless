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
  Ties fall back to input order, keeping the outcome deterministic. A rung whose
  dependency is absent is skipped, and a provider that cannot be reached for any
  candidate is skipped too rather than recorded as the decider. `JudgeResult`
  carries `decided` for exactly this: the recorded rung is read as evidence that
  a rung is alive, so neither a dead provider nor a rung that merely narrowed the
  field may look like one that chose the winner.
- The ladder dispatches through the provider seam with a **model slug**, never a
  resolved vendor id. Each adapter resolves the slug itself, which is what keeps
  the seam provider-neutral; resolving before dispatch hands an adapter a value
  it will not recognise and takes the whole rung down silently.
- `cadless/forge.py:race_and_judge` is the one composition of fan-out and
  selection. The live agent turn and the evaluation harness both call it, so a
  measurement of best-of-N is a measurement of the shipped path rather than of a
  second implementation that would drift away from it.
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
