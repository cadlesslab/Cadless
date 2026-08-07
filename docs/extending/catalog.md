# Catalog items and domains

Two different things are called "catalog" and it helps to separate them early:

- **`cadless/catalog/`** is code — the CLI, the loader, the manifest schema, the
  packer and the domain registry.
- **`catalog/`** on disk is content — the items themselves. It lives at
  `CADLESS_CATALOG_ROOT` (default `./catalog`), outside the package, and is
  bind-mounted into the containers.

**Authoring is not in this repository.** Turning source material into an item —
parsing floor plans or CAD records, generating step code, executing it into
baked artifacts, scoring the result against goldens — runs in a private
pipeline. What is here reads items and loads them. This page describes the shape
an item has to have, and how to add a domain.

## Where content lives

Each domain gets its own subdirectory of the catalog root, named
`{domain-key}-catalog` by default. The `mechanical` domain is the one exception:
it overrides the name to `mech-catalog` because history diverged.

```
catalog/                 # CADLESS_CATALOG_ROOT
  house-catalog/         # domain "house"
  mech-catalog/          # domain "mechanical" (overridden name)
    l-bracket/
```

An item is simply a directory containing `manifest.json`. Discovery scans one
level down for exactly that, so adding an item is adding a directory.

Items that arrived as `.cls` packages do not go here. That root ships with the
image and the deployment mounts it read-only, which is right — it is a product
asset, not somewhere downloads accumulate. An import lands under the data
directory instead, beside the settings and the store, in the same
`{domain-key}-catalog` layout:

```
$CADLESS_DATA_DIR/
  imported-catalog/
    mech-catalog/
      l-bracket/
```

Startup walks both roots. There is no watcher and no scan loop, so a root left
out of that walk is a root whose items are gone at the next restart.

A build that publishes may need a third place an item briefly exists, and it
should not be a root: a scratch copy for a packer to read is not content, and
anywhere outside both walked roots keeps it from being loaded. Nothing in this
tree writes one.

## What an item looks like

```
l-bracket/
  manifest.json          # metadata + the ordered steps
  source.json            # provenance (where the design came from)
  steps/01.py            # one build123d script per step
  artifacts/01/model.step   .glb  .stl  .obj    # produced when the item was authored
  artifacts/thumbnail.png
```

Everything under `artifacts/` is generated — never hand-edit it. So is
`manifest.json`'s `geometry` and `artifacts` data, which the authoring pipeline
measures and fills in.

A build that publishes produces this same shape from a project without running
any of that: it reads the ladder out of the store, copies the exports each step
already has, and renders the thumbnail from the final mesh
(`catalog/thumbnail.py`). Nothing is re-run and nothing is re-baked, so the only
things it has to ask for are the ones a project does not carry — an address, a
licence and a domain.

`source.json` also carries `derived_from` when the project it was written from
is a copy of something fetched from a listing: the `catalog_id`, `version_id`
and `digest` of the listing the line began at, and `unchanged` where the copy
still builds that listing's code verbatim. A packer copies it into `cls.json`,
where it is inside the digest and so cannot be edited off afterwards, and a
receiver keeps it only after checking it against its own records. A project that began here writes no such key — an absent one and one
holding nothing are different claims.

An imported item records the same key when the package it arrived in stated
one. Nothing reads that back today: a copy made from a received item names
*that* item's listing, which is the honest answer. It is kept because the
package is not retained after the import, so this file is the only place the
publisher's own claim survives.

`manifest.json` requires `id`, `name` and `steps`. Everything else is optional:
`domain` (defaults to `house`), `slug`, `verified`, `source`, `category`,
`tags`, `description`, `thumbnail` and the house-specific `storey_height`. Each
entry in `steps` has:

| Field | Meaning |
| --- | --- |
| `index` | 1-based position in the ladder |
| `instruction` | the natural-language instruction for this step |
| `code` | path to the step's script, relative to the item directory |
| `geometry` | `volume` / `bbox`, measured when the item was authored |
| `artifacts` | kind → relative path, likewise |
| `assertions` | optional post-conditions, e.g. `{"volume_tol": 0.05}` |
| `expected_bodies` | how many disjoint solids the step deliberately produces; absent means 1 |
| `transcript` | optional `user_prompt` / `assistant_message` pair |

Loading validates three things, so get them right or you will see the error
before anything runs: step indices must start at 1 and be contiguous, every
`code` path must exist, and `domain` must name a registered domain.

Each step's script is ordinary build123d that assigns its output to `result` —
the same contract generated code obeys, enforced by the validator and described
in [core-modules.md](./core-modules.md#changing-the-validator).

## The CLI

Everything goes through one entry point; there is no console script.

```
python -m cadless.catalog <verb> [flags]
```

| Verb | Does |
| --- | --- |
| `list` | show discoverable items and whether each is loaded |
| `load` / `reload` | load items into the running store |
| `clear` | drop loaded items |

None of them needs a model or a key, and none writes to the catalog root — they
read content and write to the live database. Which is why they run inside the
api container while the root stays mounted read-only; see
[`cadless/catalog/README.md`](../../cadless/catalog/README.md).

`--catalog-dir` defaults to the *house* directory for `load`, `reload` and
`list`, so loading a mechanical part needs it passed explicitly or the command
looks in the wrong place and finds nothing. (`clear` takes no `--catalog-dir` at
all — it works off what is already loaded.)

`--house` and `--part` are aliases selecting the same id; `--part` simply reads
better for mechanical items. Both are repeatable and may be combined.

## Measuring generation quality

Whole-pipeline eval measures the generate → validate → execute → repair loop and
reports success rate, first-try rate, repair lift, average attempts and
degenerate solids. Run it from the command line:

```bash
python -m cadless.evalkit --tier hard
python -m cadless.evalkit --tier easy --out runs/easy.json --format json
```

or from Python, which is what you want when supplying your own prompts:

```python
from cadless.evalkit import load_tier, run_pipeline_eval

report = run_pipeline_eval(prompts=load_tier("hard"))
print(report.success_rate, report.repair_lift)
```

**Two tiers ship with the package**, generated from this catalog by
`tools/build_eval_tiers.py` and version-controlled under `cadless/evalkit/tiers/`:

| Tier | Prompt source | What it measures |
|------|---------------|------------------|
| `easy` | each item's step-1 `instruction` | near-transcription — every dimension is stated. A regression floor. |
| `hard` | the item's own `description` | one line for a whole part, dimensions inferred. This is where failure headroom lives. |

`easy` covers every item; `hard` is a subset (18 of 39 today). An item joins `hard`
only if it has a `description` **and** is complex — 150+ total lines across its
step scripts, or more than one expected body. So adding a small item puts it in
`easy` alone, and an item with no `description` cannot appear in `hard` at all;
the generator prints both exclusions on every run.

Step 1 only for `easy`, because the step scripts are cumulative: a later step's
instruction describes one feature while its code re-emits everything before it.

Larger prompt sets are still data — `load_benchmark()` reads `prompts.jsonl`
under the catalog root, and **that file is not bundled with the repo**.

Before you run it: it drives the real `Pipeline`, so it burns real tokens, it is
not part of `make test`, and generation is not deterministic — one run is a
sample, not a measurement. Repeat a tier and read the spread. Note also that
`success_rate` means "built a valid solid", not "built the *right* solid";
nothing here compares the result against the item's stored geometry.

Scoring individual catalog items against their goldens is a different tool and
lives with the authoring pipeline, not here.

## Adding a domain

Domains are pure data. A new one is a `Domain` instance handed to
`register_domain`:

```python
from cadless.catalog.domains import BASE_METRICS, MESH_METRICS, Domain, register_domain

register_domain(Domain(
    key="jewellery", label="Jewellery", authoring_units="mm", sort_order=40,
    eval_metrics=BASE_METRICS | MESH_METRICS))
```

| Field | Meaning |
| --- | --- |
| `key` | the value items put in `manifest.json`'s `domain` |
| `label` | display name for UI grouping |
| `authoring_units` | units the step scripts are written in; drives `export_scale` |
| `sort_order` | UI group order, lower first |
| `eval_metrics` | which metric set per-item scoring applies |
| `content_dir` | subdirectory name; defaults to `{key}-catalog` |

`authoring_units` matters more than it looks: it becomes the authoring-units →
millimetre `export_scale` applied to exported artifacts, so declaring it wrong
produces geometry that is right in the summary and wrong in the file. The house
domain authors in metres; the mechanical, furniture and fixture domains author
in millimetres.

`eval_metrics` is declared here but read elsewhere — per-item scoring runs in the
authoring pipeline. Registering a domain without it is not an error; it means
that pipeline has nothing to score the domain by.

`register_domain` refuses to overwrite an existing key unless you pass
`replace=True`. Tests that register synthetic domains clean up with
`unregister_domain`.

Metric sets compose: `BASE_METRICS` (volume, bbox) applies everywhere,
`MESH_METRICS` compares a generated mesh against the baked golden, and
`IR_METRICS` covers the static house checks.

## Checklist

- [ ] Item directory contains `manifest.json`, under the right domain's content dir
- [ ] `domain` names a registered domain
- [ ] Step indices start at 1 and are contiguous; every `code` path exists
- [ ] Each step script assigns its output to `result`
- [ ] `list` shows the item before you try to `load` it
- [ ] New domain, if any, declares the correct `authoring_units`
