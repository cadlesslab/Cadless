# The generation pipeline

The heart of the engine is one loop: **generate → validate → execute →
repair**. This document walks that loop end to end — entry points, the module
responsible for each stage, the progress-event vocabulary, how repair decides
to keep going, where processes and threads split, and the two SSE paths that
stream it all to the browser. File references are to the modules mapped in
[architecture.md](./architecture.md).

## Entry points

All in `cadless/pipeline.py`:

```python
Pipeline.run(intent, export_dir=None, on_progress=None, prior_code=None,
             assertions=None, grounding=None, temperature=None,
             export_scale=1.0) -> GenerationResult
```

- `prior_code=None` → **fresh generation** from the intent alone.
- `prior_code` given → **refinement**: the model surgically edits the prior
  code to satisfy the change request. The validate → execute → repair loop is
  identical in both modes.
- `generate_cad(...)` is the convenience wrapper that builds a default
  `Pipeline`; `Pipeline.run_candidates(intent, n, ...)` runs N fresh
  generations in parallel for best-of-N
  ([ADR-0005](./adr/0005-best-of-n-judge.md)).

## Stages and owners

| Stage | Owner | What happens |
|---|---|---|
| interpret | `pipeline.py` | Pre-loop intent processing; emitted as attempt 0. |
| generate / refine | `prompts.py` `CodeGenerator.generate` / `.refine` | Prompt assembly (system prompt + few-shot from `few_shot.py` + optional retrieval `grounding`) and the provider call; `extract_code` pulls the fenced code out of the reply. |
| validate | `validation.py` `validate_code` | The AST static gate — sandbox layer 1 ([ADR-0003](./adr/0003-three-layer-sandbox.md)). No execution happens for code that fails here. |
| build + mesh | `worker.py` `run_code` | Sandboxed execution and artifact export. Meshing happens inside the worker alongside the build; `mesh` is reported ok once artifacts exist. |
| critique *(optional)* | `vlm_critique.py` | Renders the GLB and asks a vision model whether it matches the intent; a mismatch forces a repair. Off by default. |
| assert *(optional)* | `assertions.py` | Deterministic geometry post-conditions (`GeometryAssertions`); a failure forces a repair. |
| repair | `prompts.py` `CodeGenerator.repair` | The error (as a structured `RepairContext` for build failures) goes back to the model with the failing code; the loop retries with the repaired code. |

## The repair loop, precisely

```python
max_tries = max(1, settings.repair_max_attempts)   # default 3
for n in range(1, max_tries + 1):
    validate → (fail → repair, continue)
    build    → (fail → repair with RepairContext, continue)
    critique → (mismatch → forced repair)      # only when enabled
    assert   → (fail → forced repair)          # only when assertions given
    success  → return GenerationResult(ok=True, ...)
```

- `max_tries` counts **total attempts including the first**, not just
  repairs. The default budget is therefore: one generation plus up to two
  repairs.
- `_repair()` returns `None` (ending the loop) when the budget is exhausted —
  unless the repair is *forced* (critique/assert), and those paths only fire
  while `n < max_tries`, so a forced repair always has an attempt left to
  use.
- A `GenerationResult` carries the final code, per-attempt history, and the
  exported artifact paths on success.

## Progress events

`on_progress(event)` receives a small, stable vocabulary (defined at the top
of `pipeline.py`):

| Event | Payload | Meaning |
|---|---|---|
| `start` | — | The run began. |
| `stage` | `phase`, `status: begin\|ok\|error`, `attempt`, `error?` | Granular lifecycle. `attempt` is the 1-based try; 0 for the pre-loop interpret/generate phases. |
| `attempt` | `n` | A new try of the validate→build cycle began. |
| `codegen` | `text` | Live token deltas while fresh code streams from the provider. |

`phase` is one of `STAGE_PHASES`:

```python
("interpret", "generate", "refine", "validate", "build", "mesh",
 "critique", "assert", "repair")
```

The flow reads: `interpret → generate|refine → (validate → build → mesh
[→ critique])*` with `repair` between failed attempts.

## Execution

`worker.py:run_code` has two paths, switched by `settings.worker_url`:

**In-process subprocess** (`worker_url=""` — local dev, tests, offline
offline runs). A fresh child is spawned per run:

```
subprocess.run([sys.executable, "-m", "cadless._worker_child", ...],
               timeout=wall, preexec_fn=_limit_resources(cpu))
```

- `_limit_resources` sets POSIX `RLIMIT_CPU`; the wall-clock timeout is
  `settings.exec_timeout_secs` (default 30 s) and also drives the CPU limit.
- `RLIMIT_AS` is **deliberately not set**: OCCT reserves a very large virtual
  address range up front, so an address-space cap causes thrashing rather
  than safety. Real memory limits come from the container cgroup
  ([ADR-0003](./adr/0003-three-layer-sandbox.md)).
- The child executes the (already validated) code, exports artifacts, and
  reports back over a one-line protocol on stdout: `__VTRESULT__ {json}`.

**Remote worker** (`worker_url=http://worker:9000` — the compose stack). The
API delegates with `POST /run` to `worker/service.py`, which calls the same
`run_code` locally inside the worker container. The worker forces
`worker_url=""` for its own runs, so it can never delegate to itself.

## Threads, processes, async

- **Backend routers are async** (FastAPI). The agent loop itself is
  synchronous; the chat router drives it in a worker thread
  (`run_in_threadpool`) and bridges events back to the SSE response through
  an `asyncio.Queue` with `loop.call_soon_threadsafe`.
- **Retrieval, distillation and compaction are async** (`rag.py`,
  `distill.py`, `compaction.py` — the store is `aiosqlite`). The chat router
  runs retrieval first, then hands the resulting grounding string down into
  the synchronous pipeline.
- **Best-of-N runs candidates in a thread pool**
  (`ThreadPoolExecutor(max_workers=count)`): provider calls are IO-bound and
  execution is a subprocess, so candidates genuinely overlap.
- **Steering is cross-thread**: `SessionSteerRegistry` (in `agent.py`) holds
  a lock-protected queue per session so another request thread can inject a
  user message into an in-flight turn.

## Two SSE paths

**Chat (the agent path)** — `POST /projects/{id}/chat`
(`backend/routers/chat.py`) drives `Agent.stream_turn` (`agent.py`) and
translates its events into a neutral SSE vocabulary: `turn_start`, `steer`
(a mid-turn user message injected into the running turn — see steering
below), `text_delta`, `thinking_delta`, `tool_start`, `tool_progress` (the
pipeline `stage` events, nested), `tool_result`, `plan`, `clarification`,
`turn_end`. The router adds two events of its own: `codegen_delta` (live
code tokens, from the pipeline's `on_codegen` hook) and `error` (a failure
detail when the turn raises).

The agent exposes five tools to the model (`build_tools()`):

| Tool | Effect |
|---|---|
| `generate_model(spec)` | Fresh pipeline run. |
| `edit_model(change)` | Refinement run on the current version's code. |
| `set_parameters(params)` | Deterministic `params`-block override + rebuild (`params.py`) — no LLM in the loop. |
| `ask_clarification(questions)` | Ends the turn and asks the user (max 3 questions). |
| `submit_plan(steps)` | Streams a brief plan (max 8 steps); does **not** end the turn. |

Turn hard caps (all in `config.py`): `agent_max_tool_iters=6` tool
round-trips, `agent_token_budget=200_000` cumulative tokens,
`agent_time_budget_secs=120` wall clock, plus a duplicate-tool-call debounce
and a same-stage escalation counter (`agent_same_stage_escalation=2`) that
nudges the model toward `ask_clarification` when it keeps failing at the
same stage.

**Direct generation** — `POST /projects/{id}/generate` +
`GET /projects/{id}/generate/stream` (`backend/routers/generation.py`) runs
`generate_cad` and forwards the pipeline's `on_progress` events as SSE
without the agent in between.

Both paths rely on the bundled Caddy proxy passing SSE through unbuffered
(`flush_interval -1` on the API arm — see `infra/proxy/README.md`).

## Measuring the loop

`cadless/evalkit/` quantifies pipeline behaviour:

- `harness.py` runs a benchmark prompt set through a generator+executor pair
  and reports compile and success rates.
- `pipeline_eval.py` runs the *full* pipeline per prompt and adds
  `first_try_rate` and `repair_lift` — the extra successes the repair loop
  bought — plus average attempts and degenerate-solid counts.
- Two benchmark **tiers** ship with the package under `cadless/evalkit/tiers/`
  and are version-controlled, because engine-quality work quotes numbers
  against them and a set that can change without a diff cannot anchor a
  regression claim. `load_tier("easy")` is each catalog item's step-1
  instruction, which states every dimension; `load_tier("hard")` is the item's
  own one-line description, which leaves them to be inferred. The hard tier is
  where failure headroom lives — a benchmark everything passes cannot show that
  a quality feature helped. `easy` covers every catalog item, `hard` only the
  complex ones that carry a description (18 of 39 today). Both are generated by
  `tools/build_eval_tiers.py`, which names every exclusion as it runs.
- Larger prompt sets stay data: `prompts.jsonl` under the catalog root
  (`CADLESS_CATALOG_ROOT`), one `{id, prompt}` object per line, read by
  `load_benchmark()`.
- Run a tier with `python -m cadless.evalkit --tier hard`. Evals call a live
  provider with your key and are never part of `make test`. Generation is not
  deterministic, so a single run is one sample — repeat and read the spread
  before quoting a number.
