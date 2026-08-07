# ADR-0007: This repository is the engine and its seams; what plugs into them is not published

## Status

Accepted. Supersedes **one bullet** of [ADR-0004](./0004-local-first.md) — the
one that made the public repository the source of truth. Everything else in that
record stands: local-first, loopback-only, and bring-your-own-key are unchanged
decisions, not casualties of this one.

## Context

The tool was published as a single repository, on the assumption that everything
it would ever be was in that tree. Then the product grew a second half — a hosted
service, a catalogue people can share through, an account to sign in to — and
that half is not open source. For a short period both halves were developed in
this one repository, and the log still carried the marks of it.

Two ways out were available. The tree could keep both halves and publish
selectively, which means a scrubbing pipeline standing between development and
publication, deciding on every commit what the outside may see. Or the tree could
stop containing the second half at all, and the question would not arise.

The first is the arrangement this project already escaped once: an
extract-and-publish pipeline whose scrubbing rules are now CI (`tools/leak_guard.py`)
rather than a release-time filter. Rebuilding it would put a machine in the way
of every ordinary contribution and make "what is public" a property of a script
nobody reads, which is a worse promise to a contributor than a plain repository.

## Decision

- **This repository holds the engine and the seams, and no implementation that
  fills them.** A seam is an extension point with a published contract: the
  `cadless.routers` entry-point group, the frontend `registerPanel` registry, the
  provider protocol ([ADR-0001](./0001-provider-seam.md)) and the identity
  resolver ([ADR-0006](./0006-identity-seam.md)). What can be added from outside
  the tree is added from outside the tree.
- **The implementations live in a separate, private repository** that carries the
  hosted service and the shared catalogue, and is where a sign-in filling the
  identity seam belongs once there is one. What it installs today registers a
  router and a panel and no principal resolver, so that seam is cut and not yet
  plugged. That repository is not published, this record does not promise that it
  ever will be, and it is deliberately not named here — see the last consequence
  below.
- **The dependency runs one way and the repository boundary is what keeps it
  that way.** The platform imports the engine; the engine imports nothing of the
  platform's and does not know it exists. Merged into one tree, nothing
  structural would stop the arrow turning around — which is the whole reason the
  two were not merged.
- **The boundary has a gate, and the gate has a stated reach.**
  `tools/leak_guard.py` fails the build closed on the hosted service's domain,
  its settings key and its distribution name, so the ordinary way this boundary
  gets crossed — a file carried back over — is caught on the pull request rather
  than noticed later by a reader. Measured against the hosted half's own package,
  those patterns flag 26 of its 38 files. What the gate is not is a proof of
  absence: something written here from scratch under different names would pass
  it, and a reviewer still decides whether a change belongs in this repository.
  It closes the likely accident, not the determined author.
- **This project's own identity stays sayable; the hosted half's does not, and
  that includes its name.** Nothing in this tree names that repository, its
  directories or its modules, and the guard fails the build on those alongside
  the hosted domain and settings key. The asymmetry is deliberate: what a reader
  needs is the *shape* — engine and seams here, implementations elsewhere, the
  dependency running one way — and none of that requires knowing what the other
  repository is called. A name buys a contributor nothing they can act on, and
  it is one more string to scrub if it spreads. Where the seams are concerned
  this repository is complete on its own terms: the contracts are published, and
  anyone may fill them.
- **Publication is an act, not a pipeline.** This tree is published as it
  stands, by a person who decided to. There is no automation that pushes here on
  a schedule or on a merge, and adding one is a separate decision that this
  record does not take.

## Consequences

- **The published history will start at a fresh root, and the repository will be
  recreated to get there.** Neither half of that is sufficient alone. Rewriting
  the log does not reach a merged pull request's ref, which GitHub keeps outside
  the branch history and serves again the moment the repository is public.
  Recreating the repository does not clean the log, because the branch is pushed
  as it stands. So both, and in that order. Nothing outside is lost by it: the
  repository had no stars, no forks and no external contributors while it was
  open. **Until that act, this record states the arrangement, not the log you
  are reading** — the tree carrying this file still has the shared-period
  history behind it.
- **Contributing is unchanged and stays ordinary.** Pull requests are plain pull
  requests against `main` under the DCO, reviewed and squash-merged, with
  authorship preserved in the usual way. See [CONTRIBUTING.md](../../CONTRIBUTING.md).
- **A capability that needs the hosted half is absent here, not stubbed.** The
  tool runs whole without it — the bundled catalogue makes the first run useful
  with no key and no account ([ADR-0004](./0004-local-first.md)). What is missing
  is sharing with other people, not working.
- **Widening a seam is engine work and belongs here.** If an implementation on
  the other side needs something the contract does not yet give it, the contract
  is what changes, in this repository, in the open — not a private fork of the
  engine.
- The reverse is also true and is the cost: a reader of this repository cannot
  see how the seams are used in production, only what they promise. The
  extension guides under [docs/extending/](../extending/README.md) carry worked
  examples for that reason.
