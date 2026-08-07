# ADR-0004: Local-first, loopback-only, open source as the source of truth

## Status

Accepted, with two named exceptions. **Local-first, loopback-only and
bring-your-own-key are unchanged decisions** and are what this record is still
consulted for.

- The single-user consequence below is **amended by
  [ADR-0006](./0006-identity-seam.md)**: the engine can now be told who is
  asking, and scopes projects and the knowledge base on the answer. What it still
  does not do is authenticate anyone — the loopback decision here is unchanged,
  and the settings endpoint still stores credentials without asking who is
  calling.
- The **source-of-truth bullet alone is superseded by
  [ADR-0007](./0007-engine-and-implementations.md)**: this repository is the
  engine and its seams rather than the whole product, and the implementations
  that fill those seams are developed elsewhere and not published. The bullet is
  marked in place below.

## Context

Cadless could have launched as a hosted service. Instead the goal is a tool
anyone can run, inspect and extend: no account, no telemetry, no server-side
dependency of ours in the loop, working sample content before any API key is
entered.

## Decision

- ~~**The public repository is the source of truth.**~~ **Superseded by
  [ADR-0007](./0007-engine-and-implementations.md).** This repository is the
  engine and its seams, not the whole product: a hosted half exists, is developed
  in a separate private repository, and is not published. What survives from this
  bullet unchanged is how contributing works — development of *the engine*
  happens here in ordinary commits and pull requests, contributions are plain PRs
  under the DCO, and CI enforces a leak guard so internal references and
  credential shapes can never land in the tree.
- **The tool is local-first.** Everything runs on the user's machine via
  `docker compose`; model access is bring-your-own-key
  ([ADR-0001](./0001-provider-seam.md)); the bundled catalog makes the first
  run useful with no key at all.
- **The published port binds loopback only** (`127.0.0.1`). The settings
  endpoint stores API keys without authentication — acceptable for a
  single-user tool on localhost, and exactly why the stack must not be
  exposed. The proxy owns only its own path prefix and 404s everything else.

## Consequences

- Users can adopt the tool with zero trust in us: no data leaves their
  machine except their own provider calls with their own keys.
- Exposing the stack beyond localhost requires adding authentication and
  kernel-level isolation first ([ADR-0003](./0003-three-layer-sandbox.md));
  the default configuration refuses to make that easy.
- Single-user assumptions were baked in (one settings store, one database).
  Half of that has since been lifted — rows carry an owner and a build can
  supply a principal ([ADR-0006](./0006-identity-seam.md)) — and half has not:
  the settings store is still one file for the whole installation, and hosting
  still needs an authenticated settings boundary that does not exist yet.
- Everything needed to evaluate quality ships with the repo — the eval
  harness runs with the user's own key, and published pass rates are
  reproducible rather than claimed.
