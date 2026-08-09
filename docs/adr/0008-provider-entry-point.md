# ADR-0008: A model backend can be installed beside the engine

## Status

Accepted. Extends [ADR-0001](./0001-provider-seam.md), which recorded the
provider seam as a neutral protocol plus a registry and assumed every adapter
lived in this tree. The protocol is unchanged; what changes is where an adapter
is allowed to come from.

## Context

The four bundled adapters register themselves as an import side effect, and the
import that triggers them names modules inside this tree. That is a complete
answer for adapters this repository ships, and no answer at all for one it does
not.

Two builds need a backend that is not here, for opposite reasons. A hosted
build generates on the visitor's own credential, which the engine must never
hold — so the code that dispatches with that credential belongs to whoever
holds it, not here. An internal build wants a fork of one of these adapters
pointed at an endpoint that is not ours to publish. Neither is served by adding
a module to this tree: the first is not this repository's to write, and the
second would put a private product's name in a public log permanently.

The mechanism already exists for a different kind of extension. The engine
reads `cadless.routers` so that a distribution can add routes no list inside
this tree could have named ([ADR-0007](./0007-engine-and-implementations.md)).
Providers get the same treatment rather than a new one.

## Decision

- **The group is `cadless.llm_providers`.** The entry-point name is the
  provider name; the object it loads is the `ProviderFactory` the registry
  already had. Nothing about the provider contract widens — `ChatProvider`,
  `ProviderFactory` and `build_provider`'s signature are exactly what ADR-0001
  recorded, and code outside this tree already depends on that signature.
- **A name already registered is refused**, and taking one over has to be asked
  for (`replace=True`). The bundled adapters load before discovery runs, so the
  refusal lands on the newcomer and this tree's own adapter stays. Which model
  backend the engine generates through is not something a build should be able
  to change by installing a package, and the accident would have no symptom:
  the wrong provider answers perfectly well.
- **The two ways discovery can fail are kept apart.** A distribution that
  advertised a provider and then could not produce one is a broken install: it
  is logged, the app carries on, and the cause is kept so that selecting that
  name reports what happened. A distribution that claimed a name this tree
  already ships is refused and *nothing is recorded against the name* —
  recording it would make selecting that name report the collision instead of
  returning the adapter that is still perfectly well installed.
- **The seam is identity-free, and that is a decision rather than an
  omission.** Nothing in it names a caller, an account, a token or a key. A
  provider that dispatches on a per-caller credential reads that credential at
  construction, from a context it owns. The alternative — a principal argument
  on `build_provider` — would give the engine an opinion about who is asking,
  which is the seam [ADR-0006](./0006-identity-seam.md) already cut and
  deliberately keeps separate from this one.
- **The accounting surface is frozen.** No meter, counter, usage type, quota or
  price enters this seam or the protocol behind it. A build that wants to
  charge for generation counts where it holds the credential, which is inside
  its own provider.
- **Selection is by environment only.** `CADLESS_LLM_PROVIDER` picks the
  provider; `user_settings.PROVIDERS` stays a closed tuple of the names this
  tree ships.

## Consequences

- The local build is unchanged and needs no configuration: with nothing
  advertised, the group contributes nothing and the four bundled adapters
  behave exactly as before.
- A hosted build can generate through a credential the engine never sees, and
  an internal build can point at an endpoint this repository does not name.
- **A discovered provider is not offered in the settings panel**, and
  `user_settings.validate()` refuses it as a saved value. That is the closed
  tuple working as intended — a private provider's name never reaches a public
  API response — and the cost is that choosing one is a launch decision rather
  than something a user picks in the UI. Reconciling the two lists is left
  open on purpose; it is a question about what a hosted build shows its users,
  not about this seam.
- **Nothing outside a provider can count generation.** `complete()` and
  `embed()` are called on the provider object from seven places inside the
  engine — `cadless/prompts.py`, `cadless/judge.py`, `cadless/compaction.py`,
  `cadless/distill.py` and `cadless/rag.py` — and none of those calls crosses
  an HTTP boundary, so anything sitting in front of the engine's routes sees
  none of them. Whatever wants those numbers has to *be* the provider. That is
  why the surface above stays frozen rather than growing a hook that could not
  have worked from outside anyway.
- **Containment covers a module that exits, and discovery is safe under
  concurrent requests.** `SystemExit` is not an `Exception`, so a provider
  module calling `sys.exit()` while being imported would otherwise leave
  discovery and take the request with it; it is caught and recorded like any
  other load failure, while `KeyboardInterrupt` is deliberately still allowed
  through. Discovery is cached, and the cache is published only once the table
  is actually populated — `build_provider` runs per generation request, so a
  second caller arriving mid-discovery must wait rather than be told the work
  is done and find nothing.
- **What this seam does not contain is a provider that never returns.** A
  module-level import that hangs blocks discovery, and discovery runs inside
  the first request rather than at startup, so one such add-on wedges request
  handling. Containing that means running discovery at startup, which is a
  change on the serving side rather than in this seam. Two constraints on an
  advertised module follow, and neither can be enforced from here: its import
  must terminate, and it must not block on **another thread** that calls back
  into the registry. Re-entering on the same thread is handled; a second thread
  would wait for the discovery lock while the importing thread waits for it.
- **This repository advertises nothing in the group it reads.** Its
  `pyproject.toml` has no entry-points table and the bundled adapters keep
  registering by import, which is the same shape as `cadless.routers`: the
  engine holds the socket, and what plugs into it is not published here
  ([ADR-0007](./0007-engine-and-implementations.md)).
