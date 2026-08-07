# Changing validation, the worker and the exporters

These three modules form the path that turns model output into a solid. What
they run is code a language model wrote, so each layer has a job it is trusted
to do and the layer above assumes it did it. Change them with that in mind —
the reasoning behind the sandbox, and the deliberate absence of a kernel
boundary, is in [ADR-0003](../adr/0003-three-layer-sandbox.md); the loop that
drives them is in [pipeline.md](../pipeline.md).

| Layer | Module | Guarantee it provides |
| --- | --- | --- |
| Static gate | `cadless/validation.py` | Rejects disallowed code **without executing anything** |
| Execution | `cadless/worker.py` | Runs code in a separate process under a CPU rlimit and a wall-clock timeout |
| Export | `cadless/exporters.py` | Turns a built shape into artifact files |

One layering rule spans all three and is worth stating plainly: **build123d is
imported only inside the execution child and the exporters**, and even there
only inside functions. Nothing else in the package may import it at module
level. See the layering rules in [architecture.md](../architecture.md).

## Changing the validator

```python
validate_code(code: str) -> ValidationResult   # .ok, .reasons; truthy when ok
```

It walks the AST and executes nothing: allow-listed imports only, no banned
builtins, no dunder attribute access, and the script must assign `result`.
[ADR-0003](../adr/0003-three-layer-sandbox.md) covers why those four checks and
why this is the one layer that inspects code rather than containing it.

**The policy is data, and it does not live here.** It lives in
`cadless/api_subset.py`. `ALLOWED_IMPORT_MODULES`, `BANNED_IMPORT_MODULES`,
`BANNED_NAMES` and `RESULT_VARIABLE` drive the gate; `API_SUBSET_DOC` is the
prose the system prompt embeds. (`ALLOWED_EXAMPLE` and `REJECTED_EXAMPLE` are
test fixtures, not prompt content.) Allowing a new import is one entry in a
frozenset — you should almost never need to touch the walker.

Two cautions. Every relaxation is a security decision rather than a convenience
patch, for the reasons ADR-0003 gives.

And **the gate and the prompt are not wired together.** `API_SUBSET_DOC` is
hand-written prose. Adding `numpy` to `ALLOWED_IMPORT_MODULES` widens what the
validator accepts while the prompt still tells the model that only build123d,
`math` and `copy` are available — so the capability exists and nothing ever uses
it. Change both in the same commit.

`validate_code` is called from two production sites, and they guard different
things. The repair loop in `Pipeline.run` checks code this tool is about to run
from a model that just wrote it. `catalog.pack.verify_steps` checks code that
arrived from somewhere else, inside a `.cls` package, before an import writes it
where the catalog loader would find it — an upload gate elsewhere may have run
the same check once, and a package delivered any other way was never seen by
anyone. This build runs no such gate itself, which makes that site the only
one there is. Both
call it as the one-argument pure function it is; a change to that signature has
to be carried into the server's vendored copy as well.

## Changing the worker

```python
run_code(code, *, export_dir=None, export_scale=1.0, config=None) -> ExecResult
```

`ExecResult` carries `ok` / `error` / `timed_out`, the geometry summary
(`volume`, `bbox`, `part_count`, `manifold`, `min_wall_thickness`), the four
artifact paths, and a `repair_context` the loop feeds back to the model.

There are **two execution paths behind that one function**, selected by
`config.worker_url`:

- empty → run locally as `python -m cadless._worker_child` in a subprocess;
- set → POST the job to `<worker_url>/run` and let the worker container run it.

Anything you add has to work on both, and the remote path means your change
crosses a process boundary as JSON. Three constraints follow from that:

1. **The child talks to the parent through a sentinel line.** It prints
   `__VTRESULT__ ` followed by a JSON payload on stdout, and the parent takes
   the **last** line beginning with that sentinel. Ordinary output after it is
   harmless; two things are not. Never emit a second sentinel-prefixed line — it
   wins, and if it is malformed JSON the parse returns nothing even though a
   good payload came earlier. And never write partial output without a trailing
   newline just before the sentinel, or the payload no longer starts its own
   line and is missed entirely. New fields go in that JSON payload and must be
   JSON-serialisable.
2. **Only `RLIMIT_CPU` is set, and the absent address-space cap is deliberate**
   — see [ADR-0003](../adr/0003-three-layer-sandbox.md). Do not "fix" it by
   adding `RLIMIT_AS`; memory is bounded at the container level instead.
3. **The worker service must never delegate to itself.** `worker/service.py`
   constructs `Settings(worker_url="")` explicitly before calling `run_code`, so
   a worker container that happens to inherit a `worker_url` from its
   environment still executes locally rather than forwarding in a loop.

`export_scale` applies to exported artifacts only. The returned `volume` and
`bbox` always stay in the item's authoring units — keep that split if you touch
the summary, because the catalog goldens depend on it.

## Adding an export format

This is the smallest seam in the engine. An exporter is one function with a
fixed shape:

```python
def export_amf(result, out_dir: str, name: str = "model") -> str:
    from build123d import ...          # import inside the function

    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{name}.amf")
    ...
    return path
```

Then add one entry to the registry at the bottom of `cadless/exporters.py`:

```python
EXPORTERS = {"step": export_step, "glb": export_glb, "stl": export_stl,
             "obj": export_obj, "amf": export_amf}
```

That makes the child *produce* the file — it iterates `EXPORTERS` — but it is
not the whole change. Four places still name the four kinds explicitly, and each
one silently drops a fifth:

| Site | What it does |
| --- | --- |
| `ExecResult` in `cadless/worker.py` | declares `step_path` / `glb_path` / `stl_path` / `obj_path` — add yours |
| `run_code` and `_run_remote` | copy exactly those keys out of the child's payload |
| `GenerationResult` in `cadless/pipeline.py` | repeats the same four fields |

`backend/routers/generation.py` reads `getattr(result, f"{kind}_path")`, so it
picks a new kind up automatically — but only once `ExecResult` carries the
field. Miss that step and your artifact is generated and then thrown away at the
process boundary, which is a confusing failure to debug. The version and chat
routes recover on their own, because they scan `model.{kind}` on disk instead.

Production code always goes through the registry; the individual `export_*`
functions are called directly only by tests.

If build123d has no writer for your format, follow `export_obj` — it is written
by hand from `Shape.tessellate` rather than pulling in a dependency. Watch the
indexing if you do: OBJ face indices are 1-based.

## Test conventions

The suite is flat: every `test_*.py` sits directly under `tests/`, named after
the module it covers, with **no `conftest.py` anywhere in the repository**. There
is no shared fixture layer, and that shapes everything below.

**Markers** are declared in `pyproject.toml` and applied at module level, not
per test:

```python
pytestmark = pytest.mark.build123d
```

`bedrock`, `anthropic` and `openai` mean "calls that vendor's live API".
`build123d` means "executes real OCCT geometry" — note that `make test`
**does** run those; it excludes only the live-model markers, which is why a full
run takes minutes.

**Monkeypatch at the import site, not the definition site.** Modules bind
`run_code` at import, so patching `cadless.worker.run_code` after the fact
changes nothing for a module that already imported it. Patch where it was
imported *to*:

```python
monkeypatch.setattr("backend.routers.versions.run_code", fake_run_code)
```

The same applies to `EXPORTERS`: tests substitute the registry object on the
module that reads it.

Each test module defines its own local fake rather than importing one from a
neighbour — the one shared exception is `FakeChatProvider`, which is a
production module (see [llm-provider.md](./llm-provider.md)). Tests that need
data which is not bundled guard with `skipif` on the file's existence rather
than failing.

## Checklist

- [ ] Policy changes made in `api_subset.py`, not in the AST walker
- [ ] Any widened allow-list justified as a security decision, not convenience
- [ ] `API_SUBSET_DOC` updated in the same commit as any allow-list change
- [ ] Worker changes work on **both** the local subprocess and the remote path
- [ ] New result fields are JSON-serialisable and inside the sentinel payload
- [ ] Exactly one sentinel line, and it starts its own line
- [ ] build123d imported inside functions only, in the child or the exporters
- [ ] New export format: the `EXPORTERS` entry **plus** `ExecResult`,
      both `run_code` payload copies, and `GenerationResult`
- [ ] Tests marked at module level; geometry tests marked `build123d`
- [ ] Monkeypatch applied at the import site
- [ ] `make test` and `make lint` green; commits signed off (`git commit -s`)
