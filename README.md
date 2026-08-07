# Cadless

Describe a part in plain language, get real engineering CAD back — an exact
B-Rep solid you can export to STEP for manufacturing or STL for your 3D printer.

An LLM translates your description into [build123d](https://github.com/gumyr/build123d)
parametric Python. The code is statically validated, executed in an isolated
worker, and rendered in your browser. When it fails, the error goes back to the
model and it tries again.

```
"a mounting bracket, 60 mm wide, with two M4 holes 40 mm apart"
        ↓  LLM  →  build123d code
        ↓  static validation  →  sandboxed execution
        ↓  B-Rep solid
   STEP · STL · OBJ · GLB
```

The result is **parametric**, not a frozen mesh: every part keeps the code that
built it, so you can change a dimension and rebuild instead of starting over.

---

## Quick start

```bash
git clone https://github.com/cadlesslab/Cadless.git
cd Cadless
docker compose up --build
```

Open **http://localhost:8800**.

### It works before you add an API key

| Without a key | With a key |
|---|---|
| Browse the bundled catalog | Generate new parts from a description |
| **Change parameters and rebuild** | Automatic repair loop when code fails |
| Export STEP / STL / OBJ / GLB | Re-author existing catalog items |
| Inspect the code behind every part | |

The bundle ships 39 parametric items to start from, across four domains:
mechanical parts (brackets, fasteners, an engine set), furniture, enclosures and
fixtures, and one reference house (provenance in [CREDITS.md](./CREDITS.md)).
Most are built as a ladder of steps, so you can read how a part was arrived at
and not just its final shape. Rebuilding one is the fastest way to see what this
tool actually does — edit a dimension, watch the solid change. No account, no
key, no signup.

### Adding a key

Open **Settings** in the app and pick a provider:

| Provider | Credential |
|---|---|
| Anthropic | `sk-ant-…` API key |
| OpenAI | `sk-…` API key |
| AWS Bedrock | AWS credentials (or an instance role) |

Settings are saved locally under the app's data volume and take effect
immediately — no restart. You can also set them as environment variables (see
`.env.example`); environment variables win when both are present.

Generation quality differs by provider — build123d is less represented in
pretraining than some CAD languages, so results vary. Measured pass rates per
provider are published with each release.

**Current measurement** — 12-prompt mechanical benchmark, temperature 0, two
runs (results identical across runs):

| Provider · model | Success | First try | Notes |
|---|---|---|---|
| AWS Bedrock · claude-sonnet-4-6 | **12/12** | 12/12 | |
| Anthropic · claude-sonnet-4-6 | **12/12** | 12/12 | |
| OpenAI · gpt-4o | **11/12** | 10/12 | one part auto-repaired; one (`Wedge`) consistently misuses the build123d API |

Small benchmark, honest numbers: it covers common mechanical primitives, not
everything you will ask for.

---

## ⚠️ Read this before you expose it

This tool **executes code that an LLM wrote**. That is the whole point, and it
is also the risk.

Generated code is defended in three layers:

1. **Static validation** — an AST pass runs before anything executes. Only
   `build123d`, `math` and `copy` may be imported; `eval`, `exec`, `open`,
   `__import__`, `getattr` and friends are rejected, as is any `__dunder__`
   attribute access.
2. **Process isolation** — execution happens in a fresh subprocess with CPU,
   memory and wall-clock limits.
3. **Container** — network and filesystem isolation.

**There is no fourth layer.** Containers share the host kernel, so this stack is
not hardened against deliberately hostile code — no gVisor, no microVM. That is
an acceptable trade for a tool you run locally on your own machine with your own
prompts, and it is *not* acceptable for a shared or internet-facing deployment.

Two consequences:

- **Keep it on `localhost`.** The settings endpoint stores API keys and has no
  authentication. Do not bind it to `0.0.0.0`.
- **Catalog items contain executable code.** If you import one from someone
  else, it runs on your machine. It passes the same validation gate as generated
  code, but treat third-party items with the caution you'd give any script.

---

## What a catalog item is

Each item keeps the whole chain from intent to geometry:

```
<item>/
  manifest.json          name, tags, geometry summary, provenance
  steps/NN.py            the build123d code (this is the part that matters)
  artifacts/NN/model.*   baked STEP / GLB / STL / OBJ
  artifacts/thumbnail.png
```

Because the code travels with the geometry, an item is something you can *edit*,
not just something you can view. Rebuilding is deterministic and needs no API
key.

---

## Configuration

Every variable is optional. See `.env.example` for the full list.

> Upgrading from a pre-rename checkout: environment variables moved from the
> `VULCAN_` prefix to `CADLESS_` (same names otherwise), and the runtime
> database file is adopted under its new name automatically on first start.

| Variable | Default | Purpose |
|---|---|---|
| `CADLESS_LLM_PROVIDER` | `anthropic` | `anthropic` \| `openai` \| `bedrock` |
| `ANTHROPIC_API_KEY` | — | Anthropic credential |
| `OPENAI_API_KEY` | — | OpenAI credential |
| `AWS_REGION` | — | Bedrock region (AWS creds resolve normally) |
| `CADLESS_CATALOG_ROOT` | `./catalog` | Where catalog items live |
| `CADLESS_DATA_DIR` | `/data` | Database and saved settings |
| `CADLESS_EXEC_TIMEOUT_SECS` | `30` | Per-execution wall-clock limit |
| `CADLESS_PROXY_PORT` | `8800` | Published port |

---

## Layout

| Path | Purpose |
|---|---|
| `cadless/` | Core: provider seam, prompt assembly, validation, execution worker, exporters, repair loop |
| `cadless/catalog/` | Catalog loading, packing and the item manifest |
| `backend/` | FastAPI layer — persistence and the generation API |
| `frontend/` | Vite + three.js client |
| `worker/` | Execution-worker container |
| `infra/` | Compose and proxy configuration |

---

## Documentation

The layout table above is the surface; the deep dives live in [`docs/`](./docs/):

- [Architecture](./docs/architecture.md) — the system boundaries, hard
  constraints, runtime topology, and end-to-end execution flow
- [The generation pipeline](./docs/pipeline.md) — the generate → validate →
  execute → repair loop, its progress events, and every thread/process
  boundary
- [Extending the engine](./docs/extending/) — step-by-step guides for adding a
  model provider, an export format, a catalog item or a catalog domain, and for
  changing validation, the worker or the exporters safely
- [Decision records](./docs/adr/) — why the provider seam, the sandbox
  layers, additive embeddings, the local-first posture, the best-of-N judge and
  the identity seam are the way they are, and what this repository is and is not

## Contributing

Issues and pull requests are welcome — see [CONTRIBUTING.md](./CONTRIBUTING.md)
for the dev setup, the test suite, and the DCO sign-off (`git commit -s`).
The short version: `make test` and `make lint` before opening a PR, and CI
additionally runs a leak guard that keeps credentials and internal references
out of the tree. Conduct is covered by the
[Code of Conduct](./CODE_OF_CONDUCT.md).

## License

MIT — see [LICENSE](./LICENSE). Bundled catalog items and their provenance are
listed in [CREDITS.md](./CREDITS.md).
