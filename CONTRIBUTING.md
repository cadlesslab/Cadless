# Contributing to Cadless

Thanks for your interest in Cadless — issues and pull requests are welcome.
This document covers how to set up a development environment, run the tests,
and get a change merged.

> **A note on naming**: the project was renamed to **Cadless** — the Python
> package is `cadless` and configuration uses the `CADLESS_*` environment
> prefix. Upgrading a pre-rename checkout? See the migration note in the
> README's Configuration section.

## What this repository is, and where development happens

**This repository is the Cadless engine and its extension seams.** Natural
language in, a B-Rep solid and a STEP file out, with the whole stack — UI, API,
worker and proxy — running on your machine. It is complete on its own: the
bundled catalog makes the first run useful before you enter any API key.

**Engine development happens here, in the open.** Not in a mirror, not behind a
filter that decides at release time what you are allowed to see. What you read
is what runs, and a change to the engine is an ordinary pull request against
`main` in this repository — including changes we make ourselves.

**There is a second, hosted half, and it is not here.** An account, a shared
catalog and a marketplace to publish parts through are a separate product,
developed in a private repository, and this one neither contains it nor depends
on it. That half plugs in through the same published extension points anyone
else can use — the `cadless.routers` entry-point group, the frontend
`registerPanel` registry, the provider protocol and the identity resolver. The
dependency runs one way only: it imports this engine, and this engine knows
nothing about it. The reasoning is recorded in
[ADR-0007](./docs/adr/0007-engine-and-implementations.md).

Two things follow for you as a contributor:

- **Nothing here is a teaser.** No feature is stubbed out pending a paid tier.
  If a capability needs the hosted half it is simply absent, and its absence is
  documented rather than left as a dead button.
- **The seams are the contract, and widening one is engine work.** If an
  extension you are writing needs something the contract does not yet give it,
  that is a change to this repository and a welcome pull request — the guides
  under [docs/extending/](./docs/extending/README.md) are the starting point.

Contributing here is ordinary in every other respect: every commit is signed off
under the [Developer Certificate of Origin](#developer-certificate-of-origin),
and your authorship is preserved in the history like anyone else's.

## Getting set up

Two ways to run the project:

**The full stack (Docker)** — UI, API, worker and proxy, exactly what users run:

```bash
git clone https://github.com/cadlesslab/Cadless.git
cd Cadless
docker compose up --build
```

Open http://localhost:8800. No API key is needed to browse and rebuild the
bundled catalog.

**The Python engine only (venv)** — enough for most engine work and for the
test suite:

```bash
make install        # creates .venv and installs the package with dev extras
```

Requires Python ≥ 3.12. The heavy dependency is
[build123d](https://github.com/gumyr/build123d) (OCCT geometry kernel), which
installs from wheels.

## Running the tests

```bash
make test           # unit suite — no API keys required
make test-all       # everything, including live-API tests (needs credentials)
make lint           # ruff check + ruff format --check
make fmt            # ruff format + autofix
```

`make test` is the bar for a pull request: it excludes tests marked
`bedrock` / `anthropic` / `openai` (live model APIs), so it runs green with no
credentials. It does execute real build123d/OCCT geometry, so expect a few
minutes. CI runs the same selection.

## Evals (optional, bring your own key)

Generation quality is measured, not assumed. The eval harness runs the full
generate → validate → execute → repair pipeline over a benchmark set and
reports success rate, first-try rate and repair lift
(`cadless/evalkit/pipeline_eval.py`).

Two benchmark tiers ship in the repo under `cadless/evalkit/tiers/`, generated
from the bundled catalog by `tools/build_eval_tiers.py`. `easy` is each item's
step-1 instruction; `hard` is the item's one-line description, which is the tier
with room to fail:

```bash
python -m cadless.evalkit --tier hard
```

The prompt files are version-controlled on purpose — a benchmark that can change
without a diff cannot anchor a claim that a change improved anything. If you
change the catalog, rerun the generator; a test fails when the committed tiers
and the catalog disagree.

Evals call a live model with your configured provider and key, and burn real
tokens — they are never part of `make test`. Generation is not deterministic, so
a single run is one sample; repeat a tier and report the spread rather than a
bare number.

## Making a change

Adding a model provider, an export format or a catalog domain?
Each has a step-by-step guide under [docs/extending/](./docs/extending/) — those
are the seams designed to be extended, and following them keeps the change
local.

1. Fork the repository and create a branch from `main`.
2. Make the change. Match the surrounding style — `make fmt` formats and
   `make lint` checks, both across the Python source directories using the
   100-column ruff rules from `pyproject.toml`. CI runs `make lint` itself, so
   a tree that is clean locally is clean there. Adding a new top-level Python
   directory means adding it to `PY_DIRS` in the `Makefile`; `catalog/` is
   deliberately outside it, being hand-authored build123d content.
3. Add or update tests for any behaviour change — the suite under `tests/` is
   what lets us merge quickly.
4. Run `make test` and `make lint`.
5. Sign off every commit (see the DCO section below): `git commit -s`.

The whole tree was brought under `ruff format` in one sweep. GitHub skips that
commit in `git blame` automatically; to get the same locally, run this once:

```bash
git config blame.ignoreRevsFile .git-blame-ignore-revs
```

## Submitting a pull request

Open the pull request against `main`. The template asks for a summary, how
you tested it, and a short checklist. Four CI checks must pass:

| Check | What it checks |
|---|---|
| `ci` | `ruff` lint + format check + the unit suite + a full Docker build |
| `leak-guard` | No internal references or credential shapes in the tree |
| `leak-guard / dco` | Every commit carries a `Signed-off-by` trailer |
| `leak-guard / attribution` | No AI-assistant attribution in a commit message or the PR body |

Maintainers review and squash-merge. One logical change per PR keeps review
fast; large or speculative changes are worth an issue first.

## Developer Certificate of Origin

This project uses the [Developer Certificate of Origin](https://developercertificate.org)
(DCO) instead of a CLA: by signing off a commit you certify that you have the
right to contribute the code under the project's MIT license.

Sign off by committing with the `-s` flag:

```bash
git commit -s -m "fix: describe the change"
```

which appends a `Signed-off-by: Your Name <you@example.com>` trailer. Forgot
one? `git commit --amend -s` fixes the last commit, and
`git rebase --signoff main` fixes a whole branch.

## The leak guard

This repository began as a scrubbed extraction of a private codebase, and the
scrubbing rules now run here as CI (`tools/leak_guard.py`): internal tracker
ids, internal hostnames and paths, and anything shaped like an API key or
private key fail the build. If it fires on your PR, remove the flagged
content — keys belong in `.env` (gitignored), never in the tree. You can run
it locally:

```bash
python tools/leak_guard.py
```

## Code of conduct

Participation in this project is covered by our
[Code of Conduct](./CODE_OF_CONDUCT.md). Be kind; we're building CAD here.

## License

Contributions are accepted under the project's [MIT license](./LICENSE).
Bundled catalog items carry their provenance in [CREDITS.md](./CREDITS.md) —
an item whose origin is not recorded does not ship.
