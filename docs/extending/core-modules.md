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
| Script runtime | `cadless/script_runtime.py` | Runs a script only after the gate passes it, with allow-listed builtins and views of the allowed modules |
| Execution | `cadless/worker.py` | Runs code in a separate process under a CPU rlimit and a wall-clock timeout |
| Export | `cadless/exporters.py` | Turns a built shape into artifact files |

One layering rule spans all of them and is worth stating plainly: **build123d is
imported only inside the execution child and the exporters**, and even there
only inside functions. Nothing else in the package may import it at module
level, with two exceptions that live on the child's side:
`cadless/script_runtime.py`, which builds the view of build123d a script gets,
and `cadless/joinery.py`, the helpers a script is handed, which it imports. Both
import build123d at the top; the execution child and evalkit's in-process runner
import `script_runtime` only inside functions, so importing the api loads none
of them. See the layering rules in [architecture.md](../architecture.md).

## Changing the validator

```python
validate_code(code: str) -> ValidationResult   # .ok, .reasons; truthy when ok
```

It walks the AST and executes nothing: allow-listed imports only, by exact
name, and only names reviewed for scripts taken from build123d or `copy`; no
banned, dunder or withheld name anywhere a name is written; no builtin outside
the allow-list the script does not define itself; no dunder or private
attribute, none of the interpreter's frame, generator, coroutine, traceback or
code attributes, and none of the methods that look attributes up from text; no
attribute set but the few that name, colour and place a part; no class
definitions and no `match`; and the script must assign `result`.
[ADR-0003](../adr/0003-three-layer-sandbox.md) covers why, and why the gate and
the namespace a script runs in are one layer.

**The policy is data, and it does not live here.** It lives in
`cadless/api_subset.py`. `ALLOWED_IMPORT_MODULES`, `BANNED_NAMES`,
`INTERNALS_ATTRIBUTE_PREFIXES`, `STRING_LOOKUP_ATTRIBUTES`, `REFUSED_ATTRIBUTES`,
`WRITABLE_ATTRIBUTES`, `BUILD123D_REVIEWED`, `BUILD123D_WITHHELD`, `COPY_NAMES`,
`SCRIPT_BUILTINS` and `RESULT_VARIABLE` drive the gate, and `BUILD123D_REVIEWED`,
`BUILD123D_WITHHELD`, `COPY_NAMES`, `SCRIPT_BUILTINS` and `WRITABLE_ATTRIBUTES`
also decide what the namespace hands a script and what it checks after the
script runs; `BANNED_IMPORT_MODULES` only names, for the policy snapshot, the
modules that most need refusing. `API_SUBSET_DOC` is the prose the system prompt
embeds. (`ALLOWED_EXAMPLE` and `REJECTED_EXAMPLE` are test fixtures, not prompt
content.) Allowing a new import is one entry in a
frozenset — you should almost never need to touch the walker. The file and
`validation.py` are copied into the server as they are, so `validation.py`
takes the policy in its one `from cadless.api_subset import (` statement and
nothing else from the package.

Two cautions. Every relaxation is a security decision rather than a convenience
patch, for the reasons ADR-0003 gives.

And **the gate and the prompt are only partly wired together.** The builtins
line of `API_SUBSET_DOC` is built from `SCRIPT_BUILTINS`, but the rest is
hand-written prose. Adding `numpy` to `ALLOWED_IMPORT_MODULES` widens what the
validator accepts while the prompt still tells the model that only build123d,
`math` and `copy` are available — so the capability exists and nothing ever uses
it. Change both in the same commit.

**Not every capability is an import.** A script starts with the names
`cadless.joinery.script_namespace()` returns — today `split_and_join`, which cuts
a part at a plane and joins the halves — and calls them without importing
anything, so the allow-list stays as it is. That keeps the allow-list from
widening; it puts no wall around what is handed over. Whatever a handed-over
name holds, generated code can reach, so adding a name there is a security
decision like widening the allow-list. The worker child, evalkit's in-process
runner and the few-shot tests all start a script from
`cadless.script_runtime.script_globals()`, which adds the allow-listed builtins
and the module views to that function's names, so a name added there reaches
each of them; the prompt still has to mention it, as with an import.

**The views are the other half of the allow-list.** `import build123d` hands a
script a fresh module object holding the names in `BUILD123D_REVIEWED` that the
installed build123d has, less `BUILD123D_WITHHELD` and anything that is itself a
module; `import math` gets math's public names, and `import copy` gets
`COPY_NAMES` alone, with a `deepcopy` that takes no memo. A name a build123d
upgrade adds is therefore not handed out until someone reviews it:
`tests/test_script_reach.py` fails when the installed public names differ from
the reviewed ones, and walks what the reviewed ones lead to by attribute reads,
looking for a module, a frame or a real builtin. Review every new name, add it
to `BUILD123D_REVIEWED`, and add it to `BUILD123D_WITHHELD` as well where
scripts must not get it.

`validate_code` is called from three production sites, and they guard different
things. `script_runtime.run_script` checks every script just before it runs, in
the worker child and in evalkit's runner, whoever asked for the run. The repair
loop in `Pipeline.run` checks code this tool is about to run from a model that
just wrote it, so the reasons reach the model's next attempt.
`catalog.pack.verify_steps` checks code that arrived from somewhere else, inside
a `.cls` package, before an import writes it where the catalog loader would find
it — an upload gate elsewhere may have run the same check once, and a package
delivered any other way was never seen by anyone. All three call it as the
one-argument pure function it is; a change to that signature has to be carried
into the server's vendored copy as well.

## Changing the worker

```python
run_code(code, *, export_dir=None, export_scale=1.0, check_assembly=False,
         assembly_turn=False, config=None) -> ExecResult
```

`check_assembly` asks the child to measure how a multi-part result's parts
relate; `assembly_turn` puts the build on the assembly clock. They are separate
on purpose: the pipeline measures every build, and only a turn that asked for an
assembly earns the longer clock.

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
   JSON-serialisable. The child keeps stdout for that line alone — a script's
   `print` goes to stderr, and run as a program the child points everything else
   written to stdout at stderr, kernel messages included — and writes it through
   `_emit`; write nothing to stdout any other way. Both paths read the result
   back through one reader (`_read_result` in `cadless/worker.py`) that checks
   every field against what it holds, because a remote worker can be another
   engine build: a field it does not read is dropped, a value that is not what
   its field holds reads as not measured, and a success without a volume and box
   it can read is a failed build — as is a success from a child that did not
   exit cleanly.
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

Then add one entry to the `EXPORTERS` registry in `cadless/exporters.py`:

```python
EXPORTERS = {"step": export_step, "glb": export_glb, "stl": export_stl,
             "obj": export_obj, "amf": export_amf}
```

That makes the child *produce* the file — it iterates `EXPORTERS` — but it is
not the whole change. Five places still name the four kinds explicitly. The first
four silently drop a fifth; a test holds the last to `EXPORTERS`:

| Site | What it does |
| --- | --- |
| `ExecResult` in `cadless/worker.py` | declares `step_path` / `glb_path` / `stl_path` / `obj_path` — add yours |
| `run_code` and `_run_remote` | copy exactly those keys out of the child's payload |
| `GenerationResult` in `cadless/pipeline.py` | repeats the same four fields |
| `PART_KINDS` in `cadless/artifact_kinds.py` | the formats a catalog step's parts are counted by, kept apart so that reading a manifest imports no exporter — add yours, or `tests/test_artifact_kind_mirror.py` fails |

The first three rows hold for any new *field*, not only a new export kind. `assembly` is the worked example: it is declared on `ExecResult`, read out in **both** `run_code` and `_run_remote`, and mirrored on `GenerationResult`. `parts` (the build's parts as its tree names them) takes the same three places; being a list of plain dicts already, it is passed through as received rather than rebuilt. Two further constraints come with a field the remote path carries — `worker/service.py` returns `asdict(result)` straight to FastAPI, so the value must be a dataclass of JSON primitives or the response 500s; and it is rebuilt key by key rather than with `Type(**payload)`, because an api and a worker on different engine builds disagree about the field set and unpacking turns that into a `TypeError` that fails the whole call.

Every route that persists a build reads its artifacts off the export directory
rather than off the result. Three of them do it through
`backend/artifact_io.copy_and_register`; re-running a version reads the same
directory through `exporters.exported_parts` and does its own copy, because it
writes only kinds that are not already recorded. So a new kind is picked up from
disk on all of them, and an artifact is no longer generated and then dropped at
the process boundary when `ExecResult` is missing its field. Declare the field
anyway: it is what a caller holding a result rather than a directory reads.

A build with more than one solid writes one file per solid, named
`model_p{i}.{kind}` instead of `model.{kind}`. `cadless/exporters.part_name`
decides which and `exported_parts` in that same module reads it back — they are a
pair, kept side by side so the two spellings cannot drift apart, and the number
in the name is parsed rather than sorted, because that order is the part ordinal
each file is filed under. `backend/artifact_io` re-exports the reader for the
callers that have always reached it through there; everything that asks what a
build wrote — the copy path, the render review, the assembly guide — asks that
one function, so they cannot disagree about which files exist.

**Not every artifact kind is an export format, and the difference decides where
it is declared.** `thumbnail`, `guide` and `assembly` — two pictures and the
motion an assembly is played from, an accepted build's or the one a catalog
item carries — are things the engine made *about*
a build rather than formats the build was converted *to*: they get a `_MEDIA`
entry so the download route can serve them, and they stay out of `EXPORTERS`,
out of the frontend `ArtifactKind`, and out of the export menu. Putting one in
`EXPORTERS` would also put it on the `model_p{i}` naming path above, where
`exported_parts` finds nothing and the file is written, ignored, and never
served — with nothing raising. Such a kind is registered by name in
`copy_and_register` instead, which is what keeps the ordinal-by-write-order rule
intact for it. `assembly_glb` and `assembly_step` (`artifact_kinds.ASSEMBLY_FILES`)
are kept out the same way for the second reason alone: each is one file for the
whole build, written by `export_assembly` beside the parts of a build that has
several, so the per-part naming does not describe it either. They are served on
fixed addresses of their own, and the numbered route refuses them.
`tests/test_artifact_kind_mirror.py` holds the declarations together and states
which of them are containments rather than equalities.

Re-running refuses such a version, and `backend/routers/versions.py` has two
separate guards for it because they catch different populations. The first reads
the recorded rows and declines before executing anything: more rows than kinds
means the version is already held in pieces. The second catches the one the row
count cannot see — a version recorded before a build could hold several files of
a kind has exactly one row per kind whatever its geometry, so a model that was
always several solids only declares itself after rebuilding. That rebuild runs
into a staging directory, never the version's own, which is what makes the second
guard safe to reach: it discards a rebuild rather than having already overwritten
what the version was serving.

Per-part formats always go through the registry; the individual `export_*`
functions are called directly only by tests. `export_assembly`, which writes the
whole-build files, is the one exporter production calls by name.

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
- [ ] Any widened allow-list, builtin or handed-over name justified as a
      security decision, not convenience
- [ ] `API_SUBSET_DOC` updated in the same commit as any allow-list change
- [ ] Scripts run only through `script_runtime.run_script`, never another
      `exec` (`tests/test_script_reach.py` holds `cadless/`, `backend/` and
      `worker/` to that one)
- [ ] A build123d upgrade's new public names reviewed and added to
      `BUILD123D_REVIEWED`, and to `BUILD123D_WITHHELD` too where scripts must
      not get them
- [ ] Worker changes work on **both** the local subprocess and the remote path
- [ ] New result fields are JSON-serialisable and inside the sentinel payload
- [ ] Exactly one sentinel line, and it starts its own line
- [ ] build123d imported inside functions only, in the child or the exporters
- [ ] New export format: the `EXPORTERS` entry **plus** `ExecResult`,
      both `run_code` payload copies, and `GenerationResult`
- [ ] Tests marked at module level; geometry tests marked `build123d`
- [ ] Monkeypatch applied at the import site
- [ ] `make test` and `make lint` green; commits signed off (`git commit -s`)
