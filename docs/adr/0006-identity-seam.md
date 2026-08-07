# ADR-0006: The engine is told who is asking, and never how

## Status

Accepted. Amends [ADR-0004](./0004-local-first.md), which recorded that
single-user assumptions were baked in and multi-user hosting was out of scope
for the engine. Half of that is now wrong and the other half is still right:
the engine can be *told* who is asking, and it still implements no sign-in.

## Context

Everything the tool stores belonged to whoever was running it, because only one
person ever was. That is true of a tool on a laptop and false of anything hosted
for several people, and the gap is not a feature this engine wants to close
itself — an engine that knew about sessions, tokens or providers would be an
engine with an opinion about how people prove who they are, which is exactly the
part a deployment needs to bring.

The two seams that came before this one — `cadless.routers` and `registerPanel`
— can both be filled from outside the tree because they add things. This one
could not: `projects` had no owner column, so a package installed beside the
engine had nothing to scope on and no way to add it. The socket had to be cut
here even though the plug is fitted elsewhere.

## Decision

- **A `Principal` is a key and a label, and nothing else.** The key is opaque
  and compared only for equality. The engine never parses it, so a build may use
  a user id, a handle or a tenant. Nothing in `cadless.identity` names a token,
  a cookie or a provider.
- **A build registers one resolver**, the way it registers a router or an
  origin. A second registration is refused rather than taking over: two add-ons
  disagreeing about who the caller is would answer the security question twice
  and use whichever imported last, and neither would be able to tell it had lost.
- **`Store` is the engine's own API and is unscoped. `ScopedStore` is what a
  request gets.** The scoped view has no way to reach the methods that act for
  the whole installation, so a route cannot call them, and a method added to the
  store later is not reachable from a route until somebody puts it on the view
  deliberately. `tests/test_store_surface.py` refuses to let the two drift apart
  without a written reason.
- **Filtering happens in SQL.** The rule lives in one function and is rendered
  into each statement; nothing post-filters in Python, where a check standing
  beside a write has nothing holding the answer still between them.
- **A catalogue item belongs to the build.** Both roots the loader walks are
  shared directories, so ownership does not depend on which caller triggered the
  load. What somebody makes from an item is theirs, because customising is a
  clone.
- **Reading the build's rows is not permission to change them.** The rule that
  answers "what may this caller see" and the one that answers "what may this
  caller change" are separate, and the second is narrower by exactly the build's
  own key. Without that split, every principal could rename or delete what every
  other principal reads, and the only thing standing in the way would be a
  router-level guard that has to be remembered at each route — the shape of
  failure this seam exists to remove.
- **Failure is a refusal, not a fallback.** A resolver that raises answers 503.
  A deployment that sets `CADLESS_REQUIRE_IDENTITY` and has no resolver refuses
  to start.

## Consequences

- The local build is unchanged and needs no configuration: with no resolver,
  every request is the one local user, and an existing database opens with its
  projects intact.
- A hosted build can keep people's work apart without the engine learning
  anything about how they signed in.
- **Loopback-only still stands** ([ADR-0004](./0004-local-first.md), invariant 2
  in [architecture.md](../architecture.md)). This seam scopes *rows*; it does not
  authenticate the settings endpoint, which still stores provider credentials
  without asking anybody who they are. Exposing the stack beyond localhost needs
  that endpoint dealt with first, and this ADR does not do it.
- **Three things stay installation-wide** and are known gaps rather than
  oversights. A build that adds a credential of its own to that file, or a
  route of its own that curates the catalogue, adds to the list:
  1. `user_settings.json` — one file, so one person's save changes the model and
     budget for everyone, and the endpoint that writes it asks nobody who they
     are.
  2. The artifact blob directory (`artifacts_dir/<version_id>/`, flat, with
     paths already recorded in rows). Access is gated in SQL, so the layout
     leaks nothing on its own — but a build that serves those files by a route
     of its own would bypass that gate.
  3. **Who may curate the catalogue.** `POST /packages/import` and
     `DELETE /catalog/{id}` act for the installation, because a catalogue item
     belongs to it. This engine has no
     notion of privilege, so it cannot tell a caller who may do that from one
     who may not, and it does not invent one here — that is a decision about
     roles, which is the plug rather than the socket. **A hosted build that
     admits untrusted callers must gate or replace those two routes**, which
     the router seam allows without editing this tree. The rows themselves are
     safe from ordinary requests: only the named widening reaches them.
- Per-principal catalogue storage would need the directories split as well as
  the rows, and is the same piece of work as the blob layout.
