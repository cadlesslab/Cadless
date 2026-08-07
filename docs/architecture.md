# Architecture

## High-Level Mental Model & Boundaries

Cadless turns a natural-language part description into build123d Python, checks
that generated program, executes it under resource limits, and exports an exact
B-Rep model to browser and manufacturing formats.

```text
React client -> FastAPI API -> provider-neutral AI engine
                         |              |
                         |              +-> LLM provider adapters
                         +-> isolated worker -> build123d/OCCT -> artifacts
```

| Boundary | Responsibility |
| --- | --- |
| `frontend/` | Vite/React UI, REST and SSE client, three.js GLB viewport |
| `backend/` | HTTP, persistence coordination, generation streaming, and settings |
| `cadless/` | Framework-independent generation, validation, execution, export, provider, and catalog logic |
| `worker/` | Thin service boundary around resource-limited generated-code execution |
| `catalog/` | Versioned executable catalog content and baked artifacts |
| `cadless/catalog/` | Catalog loading and the item manifest; reading a `.cls` package (`pack`). This build does not assemble one — core import depends only on reading, and a build that publishes takes its definitions from `pack` |

`docker-compose.yml` connects the browser-facing proxy and frontend to the API,
then places the worker on an internal network. Only the proxy publishes a host
port, bound to loopback.

## Architectural Invariants (HARD CONSTRAINTS)

1. The interactive `Pipeline` MUST pass freshly generated or refined model code
   through `cadless.validation.validate_code` before `run_code`. The validation
   tests enforce that path's import, name, and dunder-access policy.
2. The public stack MUST remain loopback-only unless an authenticated settings
   boundary and stronger hostile-code isolation are added. The current settings
   endpoint stores provider credentials without authentication, and containers
   share the host kernel. Owner-scoping the rows (invariant 7) does not relax
   this: it decides who may read what, not who is allowed to reach the port, and
   `tests/test_loopback_bind.py` still holds the binding.
3. `cadless/` MUST NOT depend on `backend/` or `worker/`. Both services consume
   the engine as a library; the catalog CLI does the same.
4. Chat and embedding provider SDK integrations MUST stay behind
   `cadless/llm/providers/`. The code-generation pipeline and agent communicate
   through the neutral types and provider protocol under `cadless/llm/`.
5. build123d execution MUST stay behind the worker/subprocess boundary. Static
   validation is a separate caller responsibility: `run_code` does not enforce
   it. Upstream orchestration treats programs as text rather than importing and
   running them in the API process.
6. What the unauthenticated settings endpoint can change MUST stay tiered.
   Runtime tuning is settable; anything that multiplies per-turn spend is
   settable only when the launch environment opts in; and configuration that
   moves where code executes, relaxes a sandbox limit, or invalidates stored
   embeddings is never registered and is refused by the
   request model rather than accepted and discarded. Tuning values MUST NOT be
   exported to `os.environ`, because the worker spawns generated code with the
   parent environment inherited. Tests enforce each of these.

7. A request MUST reach persistence through the per-request scoped view, never
   through `Store` itself. The view carries the caller's principal into every
   query and cannot be widened back; `Store` stays unscoped for startup, the
   catalogue CLI and housekeeping, which act for the whole installation. Every
   public store method is therefore either scoped and exposed on that view, or
   exempt with a recorded reason — enforced by `tests/test_store_surface.py`,
   which also refuses to let a router import the unscoped store. Filtering is
   applied in SQL rather than after it. The engine learns *who* is asking and
   never *how*: identity is supplied by a registered resolver, and a missing or
   failing one is a refusal rather than a fall back to the local user.

The provider seam, sandbox layers, local-first posture, embeddings behavior,
candidate judging, and the identity seam decisions are recorded under
`docs/adr/` and guarded by the corresponding tests.

## Data & Execution Flow

1. The client submits an intent to the FastAPI API and receives progress over
   SSE.
2. The engine assembles the prompt and asks the selected provider for build123d
   source through the provider-neutral interface.
3. Static validation rejects disallowed syntax and imports before any execution.
4. `cadless.worker.run_code` sends the program to the worker service when
   `CADLESS_WORKER_URL` is configured; local development and tests use a
   resource-limited subprocess fallback.
5. The worker executes build123d and writes STEP, GLB, STL, and OBJ artifacts to
   the shared data volume. The API persists metadata and serves the artifacts.
6. An execution failure can return to the provider as a repair prompt. A success
   is stored as a project version whose source remains available for parameter
   changes and deterministic rebuilds.

Catalog rebuilds enter at the validation/execution boundary without an LLM. The
catalog authoring runs in a private pipeline; runtime containers mount
catalog content read-only.

## Anti-Patterns (DOs & DON'Ts)

- **DO** add provider behavior behind the provider protocol and registry.
  **DON'T** import a vendor SDK into pipeline, agent, or prompt modules.
- **DO** validate untrusted model or package source at its ingress before calling
  the raw execution primitive. **DON'T** assume `run_code` validates its input or
  call `exec`, build123d, or the worker child directly from an HTTP handler.
- **DO** keep HTTP validation and transport in `backend/` and geometry logic in
  `cadless/`. **DON'T** move reusable engine behavior into a router.
- **DO** persist source alongside geometry. **DON'T** treat a baked mesh as the
  canonical representation of a catalog item or project version.
- **DO** use `docs/extending/` for established extension seams. **DON'T** infer a
  new provider, exporter, validation, or worker procedure from a module listing.

## Gotchas & Non-Obvious Context

- The API and worker share the data volume because the worker writes artifacts
  that the API serves. The catalog mount is separate and read-only at runtime.
- `CADLESS_WORKER_URL` changes the process boundary, not the pipeline contract.
  An empty value intentionally selects the subprocess path used by tests.
- SSE must remain unbuffered through the proxy or progress appears only after a
  generation has finished.
- Catalog items contain executable source. Third-party catalog content crosses
  the same trust boundary as generated source even though it can rebuild without
  an API key.
- `run_code` is deliberately a raw execution primitive. The interactive
  pipeline validates first, while the catalog loader executes committed step
  code directly. Review those call sites before
  changing who can invoke them or what inputs they accept; their current shape
  is not evidence of a universal validation gate.
- Environment settings take precedence over settings persisted by the UI. A
  saved value may therefore be present without being the effective value.
- The gate over the cost-multiplying settings is read from the launch
  environment once at process start, so it is a deployment decision rather than
  a UI affordance — an interface that hides a control is still one request away
  from it. It blocks raising spend rather than lowering it, so whoever turned
  something on can always turn it off, and closing the gate also stops a raise
  saved while it was open from being replayed at the next boot.
- A `Pipeline` copies the settings it was built with, so a turn stays
  attributable to one configuration even though applying a setting mutates the
  shared singleton in place. Grounding retrieval runs outside the pipeline and
  is handed that same snapshot rather than re-reading the live values.
- The optional `VlmCritic` is an existing exception to ADR-0001's broad vendor
  SDK wording: it lazy-loads the Bedrock SDK directly instead of using the
  `ChatProvider` seam. It is off by default and is not the reference pattern for
  adding chat or embedding providers.
- Routes and panels can arrive from outside this tree. `backend/app.py` includes
  any router advertised under the `cadless.routers` entry-point group, so an
  installed distribution adds API routes — and its own startup work — to the
  running app. Built-in routers register first, so an add-on cannot shadow a path
  this tree already serves, and a failure while loading or starting one is
  contained to that router rather than taken as a reason to stop booting. The
  frontend reaches the same end by different means, and the difference is not
  cosmetic: a bundle is assembled from source, so it can discover nothing at
  runtime. A panel is registered through `frontend/src/panels/registry.ts` and
  reaches the build by being placed under `frontend/src/plugins/`, which
  `frontend/src/panels/plugins.ts` globs while the bundle is built. What such a
  panel may import is `frontend/src/plugin.ts` and nothing else — that file is
  the frontend's public contract, and everything outside it is internal. The
  procedures are in `docs/extending/README.md`.
- What such a build may *record* is a seam too, and for the same reason: it
  cannot add a column or a branch from outside. How an item arrived is a
  registry (`cadless/catalog/origins.py`), where an entry brings the reader that
  recognises its own records — so the vocabulary a fetch writes and the code
  that reads it back stay together, outside this engine. What a build remembers
  about a project is a row keyed by that build (`project_plugin_data`), which
  the engine stores and never reads. An item recorded by a build that is not
  installed reads as `unknown` rather than being assigned one of the arrivals
  this build does implement.
- `docs/pipeline.md` is the detailed source for repair, progress, thread, and
  process sequencing; this document intentionally does not duplicate it.

## Known Unknowns

- TODO: Decide whether VLM critique remains an explicit Bedrock exception or moves behind a provider-neutral vision protocol / Current basis: `cadless.vlm_critique.VlmCritic` lazy-imports `boto3` and `Pipeline` invokes the injected critic, while ADR-0001 says vendor SDKs live only in provider adapters / 해소 조건: Record the exception in an ADR or implement and test a neutral vision-provider seam, then align ADR-0001
