# ADR-0010: A release is cut by merging the release pull request

## Status

Accepted. Amends **one bullet** of [ADR-0007](./0007-engine-and-implementations.md)
— the one that said no automation pushes here. Everything else in that record
stands: this repository is the engine and its seams, what plugs into them lives
elsewhere, and nothing here names it.

## Context

The engine had one version, `1.0.0`, written by hand in five places, and one
release, the root commit. Anything built on the engine had to name a commit,
because a commit was the only unit there was — so "which engine is this?" had no
shorter answer than a hash, and moving to a newer engine meant reading the log to
decide which hash was safe to move to.

What someone building on the engine needs is a version: a number that says what
kind of change happened since the last one, and a release that carries it.
Producing that by hand is a chore that quietly stops happening — leaving a version
unbumped takes no decision at all. Yet everything it needs is already in the log:
every pull request here is squash-merged under a title that says what kind of
change it is.

[ADR-0007](./0007-engine-and-implementations.md) recorded that publication is an
act, not a pipeline, and that adding automation would be a decision of its own.
This is that decision.

## Decision

- **The version is computed; a person decides when it ships.** Once a change that
  releases has landed on `main`, a workflow opens one *release pull request*
  carrying the next version and the changelog since the last release, both
  computed from the squash-merged subjects, and refreshes it whenever either
  would change. Merging that pull request is the act of releasing: the run it starts
  tags `vX.Y.Z` and publishes the GitHub Release. Nothing is tagged or released
  that a maintainer did not merge into `main`, and a release pull request left
  open publishes nothing.
- **A subject says what kind of change it is.** Pull-request titles follow
  `type(scope)!: description`, and a required check refuses a title that does not,
  or that carries an internal reference or assistant attribution: the title
  becomes the subject on `main`, and the subject becomes a changelog line. `feat`
  raises the minor version, a `!` raises the major, and `fix`, `improve`, `perf`
  and `revert` the patch; `refactor`, `docs`, `test`, `build`, `ci` and `chore`
  cause no release and print no line on their own.
- **The version is written once**, as `__version__` in `cadless/__init__.py`. The
  build, the API and the worker read it there; the release pull request bumps it
  there and in the manifest it counts from, and a test holds the two equal.
- **The automation names no consumer**, exactly as ADR-0007 requires. A release is
  published here for anyone; what takes it is not this repository's concern and
  is not named in it.
- **The workflow runs on a GitHub App's token, scoped to this repository, and the
  App's key is held by an environment only `main` can use.** The default token
  may not open pull requests here, and a pull request it opened would start none
  of the required checks; a key the repository's own secrets held could be read
  by a workflow pushed on any branch.

## Consequences

- **A merged title is public text twice over** — as the subject on `main` and as a
  line in the changelog. The title check is where a wrong one is caught; after the
  merge it is history.
- **Tags are never created or pushed by hand.** A tag the workflow did not create
  is a release nobody merged.
- **Squash merging is the only merge method, and the squash subject is the
  pull-request title.** Otherwise a one-commit pull request's subject would be its
  commit message, which the title check never sees.
- **Part of this rests on repository settings rather than on the tree** — the
  merge method, the squash subject, `pr-title` among the required checks, and the
  App's key in its environment. The runbook lists them; until they are set, the
  title check advises rather than refuses.
- **Contributing is otherwise unchanged** ([ADR-0007](./0007-engine-and-implementations.md)):
  the DCO, the leak guard and the review stay as they were, and the release
  pull request's commit is signed off by the release bot like any other.
