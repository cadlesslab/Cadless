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
- `rebuild(code, export_dir=None, assembly=None, describe=True) -> Rebuild`
  runs code that is not the model's to repair — a parameter edit — once, and
  judges its parts as the loop judges its last attempt, with no model call: the
  guide it writes names unnamed parts by number.

## Stages and owners

| Stage | Owner | What happens |
|---|---|---|
| interpret | `pipeline.py` | Pre-loop intent processing; emitted as attempt 0. |
| generate / refine | `prompts.py` `CodeGenerator.generate` / `.refine` | Prompt assembly (system prompt + few-shot from `few_shot.py` + optional retrieval `grounding`) and the provider call; `extract_code` pulls the fenced code out of the reply. |
| validate | `validation.py` `validate_code` | The AST static gate — sandbox layer 1 ([ADR-0003](./adr/0003-three-layer-sandbox.md)). No execution happens for code that fails here. |
| build + mesh | `worker.py` `run_code` | Sandboxed execution and artifact export. Meshing happens inside the worker alongside the build; `mesh` is reported ok once artifacts exist. |
| critique *(optional)* | `vlm_critique.py` | Renders the build — every part it exported, drawn together in the positions they hold in the assembled model, so a split is reviewed as the thing it is rather than as its first piece — from several named views at one shared scale, and asks a vision model, through the `ChatProvider` seam, whether the shape matches the intent; a mismatch forces a repair. Every attempt is reviewed, the last one included — with no budget left the finding is reported and the part is delivered with it attached, so "ran out of budget" is distinguishable from "was never checked". On by default, but only where a critic was injected: a pipeline built without one never critiques, whatever the setting says. The best-of-N fan-out opts out — it has no progress sink, so a candidate's captures would be thrown away as they were produced while the cost multiplied by the candidate count — so the fan-out's own attempts are not reviewed here. A forge turn gets that signal from the judge's vision rung instead — handed this pipeline's reviewer and gated on the same setting — whenever more than one candidate survives the hard filter, a race settled before then having no tie to break. So an installation with the review switched off pays nothing on the path that runs N candidates, and one with it on pays for a review of the field rather than of each attempt. A critique that cannot be taken is reported and skipped, never fatal to a build that already succeeded. |
| assembly | `assembly_check.py` | Whether a multi-part build actually goes together. Runs once the model produced more than one part, whatever the turn asked; a one-part build is never measured and pays nothing, except that on a turn that asked for an assembly it is still held to the bed, in a part's own words, and refused when it does not fit: the model giving up on the split is the one answer the per-part checks never see. A turn that asked and whose request gave an overall size (`requested_mm` below) has every build held to that size too, whatever came back — one part, a measured split, or parts nothing could measure: the whole box, in millimetres and in any orientation, within a millimetre or one percent per side, whichever is larger, and refused otherwise, since a model shrunk until its parts fit passes every check of the parts. A parameter change in the same turn is held to it as well, until the user sends another message into the running turn, so a size refused for a build cannot come back through the parameters; a later turn starts with nothing pinned. A turn that asked for an assembly — by opting in, or by editing a model the store records as one — is held to every check against its printer: every part inside the build volume, no two parts sharing interior volume, the parts mating into one connected assembly, and an order in which each can be placed without collision. Any other build is held to the second alone: the bed is `print_fit`'s question for a model nobody asked to split, parts that touch nothing are not wrong in an exploded view, and a part that cannot come out is not wrong in one printed in place. Runs on **every** attempt including the last, like the critique and unlike `assert`. On an assembly turn it then refuses the build, where the critique hands it over: two parts in the same space is a measurement, not an opinion, and a check that could not be established refuses too, the opposite of `assert`'s skip-rather-than-fail policy and deliberate. Any other build is repaired while attempts remain and handed over on the last with the verdict on it, and a check that could not be established there is reported and not repaired — it found no fault for a repair to fix; the orchestrator is told the verdict, and the chat shows the findings whether the build was kept or refused, saying which. The measurement itself happens in the worker child, on the scaled solids, because that is the only place the parts exist (a lone part's fit is read from the worker's whole-shape box, brought to millimetres by the turn's export scale), and it takes a share of the time left on the worker's clock rather than of the whole of it. The check's sentences and the guide call a part what its build named it. Only a generated build on a turn that asked has the parts it left unnamed named by the guide writer's model; any other build, a parameter change included, numbers them. |
| assert *(optional)* | `assertions.py` | Deterministic geometry post-conditions (`GeometryAssertions`); a failure forces a repair. |
| guide *(optional)* | `assembly_guide.py` + `guide_writer.py` | Describes a multi-part build once the checks have accepted it: the order and the joints come from the assembly check, the names from the build where it labelled its parts, from a model for the parts a generated build left unnamed on a turn that asked for an assembly, and part numbers otherwise, and the drawings from the exported meshes — one exploded view of a small assembly, a frame per step of a larger one. Runs last, and alone among the post-build stages it can never end a turn: the parts are built and accepted by the time it starts, so what can go wrong is graded rather than fatal — a model that declines costs only the names, a render that fails costs only the pictures, and only a writer that raises costs the description. The build stands in every case. A model is asked only what to call the parts, so a bad answer costs vocabulary and cannot cost the sequence. Before the description it writes the measured order, each part's release heading and how far each part travels beside the exports (`assembly.json`, the motion the viewport plays): the length the part is engaged with the parts placed before it plus a spacing, measured from the exported meshes, which the drawings use too. Meshes that cannot be read cost only that travel, and the file still carries the plain spacing; it stands without the description, and a build that writes none sweeps the last build's away. |
| repair | `prompts.py` `CodeGenerator.repair` | The error (as a structured `RepairContext` for build failures) goes back to the model with the failing code; the loop retries with the repaired code. |

## The repair loop, precisely

```python
max_tries = max(1, settings.repair_max_attempts)   # default 3
for n in range(1, max_tries + 1):
    validate → (fail → repair, continue)
    build    → (fail → repair with RepairContext, continue)
    critique → (mismatch → forced repair)      # only when enabled
    assembly → (fail → forced repair, or on the last attempt refuse
                on an assembly turn, hand over otherwise)
                                               # only once there are several parts
    assert   → (fail → forced repair)          # only when assertions given
    guide    → (writer or render fails → skipped, never fatal)
                                               # only on an accepted assembly
    success  → return GenerationResult(ok=True, ...)
```

- `max_tries` counts **total attempts including the first**, not just
  repairs. The default budget is therefore: one generation plus up to two
  repairs.
- A reply the provider stopped at its output-token limit fails validation as
  "cut off", with the limit named, rather than as the syntax error the cut
  left; the repair is asked for a shorter script. A call on an assembly turn
  asks for `assembly_max_tokens` (default 6000) in place of
  `bedrock_max_tokens` (default 2000), because the script the brief asks for
  does not fit the smaller cap.
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
 "critique", "assembly", "guide", "assert", "repair")
```

The flow reads: `interpret → generate|refine → (validate → build → mesh
[→ critique] [→ assembly] [→ assert] [→ guide])*` with `repair` between
failed attempts.

A phase reaches the progress display only if `STEP_DEFS` in
`frontend/src/panels/progress.ts` maps it: the lookup there drops an unknown
phase silently, so one added here and not mirrored renders as nothing at all.
`tests/test_stage_phase_mirror.py` fails when the two sides disagree, and a
phase deliberately not shown is written down in its `_NOT_DISPLAYED` map.

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
`turn_end`. The router adds three events of its own: `codegen_delta` (live
code tokens, from the pipeline's `on_codegen` hook), `critique` (each render
critique round — the verdict and the views the reviewer was shown — relayed
live from the agent's `on_critique` hook) and `error` (a failure detail when
the turn raises). A `tool_result` carries `assembly_findings`
— the assembly check's failures and the lines it could not establish, as the
check worded them — whenever the verdict has any, and the chat shows them,
saying whether the build was kept, since the assembly stage above both refuses
builds and hands them over with findings. The stored tool result holds the same
verdict, so a reloaded transcript shows them too.

The agent exposes five tools to the model (`build_tools()`):

| Tool | Effect |
|---|---|
| `generate_model(spec, requested_mm)` | Fresh pipeline run. After an `edit_model` or `set_parameters` call in the same turn it regenerates the model being edited instead: it is held to that model's assembly spec and recorded as an assembly when the model was one, until the user sends another message into the running turn. `requested_mm`, optional, is the overall width, depth and height the user gave, in millimetres. On a turn that asked for an assembly the first one stated holds for every build after it until the user sends another message into the running turn, so a later call that restates the request smaller is still judged against the request. |
| `edit_model(change, requested_mm)` | Refinement run on the current version's code, with the same optional size. |
| `set_parameters(params)` | Deterministic `params`-block override + rebuild (`params.py`, `pipeline.rebuild`) — no LLM in the loop. Its parts are measured and judged as a generated build's last attempt is, held as well to an overall size a build call pinned earlier in the same turn, and its files land in the turn's export directory. |
| `ask_clarification(questions)` | Ends the turn and asks the user (max 3 questions). |
| `submit_plan(steps)` | Streams a brief plan (max 8 steps); does **not** end the turn. |

A turn may carry **reference images** alongside (or instead of) its text. They
are gated at the request boundary — format, per-image and per-turn size, count,
and whether the configured models can read one at all — because a refusal raised
inside a running turn would revert the project's version. Past that gate they
ride the neutral seam to *every* codegen call of the turn: the fresh run, a
refinement, each repair round, and each best-of-N candidate. The bytes are scoped
to that turn; what later turns are given instead is a written reading of the
picture, extracted from the codegen reply and stored beside the block
([ADR-0009](adr/0009-images-are-additive.md)).

Turn hard caps (all in `config.py`): `agent_max_tool_iters=6` tool
round-trips, `agent_token_budget=200_000` cumulative tokens,
`agent_time_budget_secs=120` wall clock, or `assembly_agent_time_budget_secs=300`
on a turn that carries an assembly spec, asked for or inherited (each build
there writes a multi-part script, runs the repair loop on it and then measures
every part, so the plain budget leaves no room to retry a refused build), plus
a duplicate-tool-call debounce and a same-stage escalation counter
(`agent_same_stage_escalation=2`) that nudges the model toward
`ask_clarification` when it keeps failing at the same stage. A turn a cap stops
ends with a line naming the cap, in the transcript and in the stream, as a reply
the provider cut at its output limit does.

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
- `--forge-n N` races N candidates per prompt instead of generating one, through
  the same `forge.race_and_judge` the live agent turn uses. Sharing that one
  function is deliberate: an A/B against a second implementation measures the
  copy, and the copy is the thing that drifts.
- **A race is judged, so what the judge can see decides what the numbers mean.**
  The report carries a `rung_distribution` saying which rung settled each
  selection. `input-order` means no rung settled it at all and the "winner" was
  whichever candidate came back first — N times the spend for an arbitrary pick,
  and a run to discard rather than quote. A rung that merely narrowed the field
  is not counted as having decided, so the distribution answers "how much of the
  ladder is alive" rather than "how far down it did we get". `assertions` and
  `vlm` appear once the geometry and render rungs have something to work with;
  until then `llm` is the only rung that can break a tie, and it ranks code
  rather than geometry.
- **A judge that cannot be reached does not fail the run**, by design: one dead
  judge must not sink a turn that generated fine. It logs a warning per failed
  scoring and the selection falls through to `input-order`, so an all
  `input-order` distribution on a run you expected to be judged is the signal to
  check the provider and its credentials. Constructing the provider validates
  only its *name* — the adapters build their client lazily, so a missing key
  surfaces on the first scoring call rather than before the first paid prompt.
- **Weigh a lift against `candidate_attempts`, not against the prompt count.**
  The rate metrics stay winner-based so they compare directly with a single-run
  baseline, which means the race's cost is deliberately *not* folded into them —
  it is reported beside them instead. A lift that does not clear N times the
  spend is not a win.
