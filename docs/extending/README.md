# Extending Cadless

The engine is built around a handful of deliberate seams. If what you want to
add fits one of them, the change is small and local — these guides are the
procedures for each.

| You want to | The seam | Guide |
| --- | --- | --- |
| Support a new model backend | `ChatProvider` protocol + `register_provider` | [llm-provider.md](./llm-provider.md) |
| Add an artifact format (STEP, GLB, STL, OBJ, …) | the `EXPORTERS` registry | [core-modules.md](./core-modules.md#adding-an-export-format) |
| Add a catalog item | a directory with a `manifest.json` | [catalog.md](./catalog.md) |
| Add a catalog domain | `register_domain` | [catalog.md](./catalog.md#adding-a-domain) |
| Change what generated code may do | the policy data in `api_subset.py` | [core-modules.md](./core-modules.md#changing-the-validator) |
| Change how generated code is executed | `run_code` and the worker | [core-modules.md](./core-modules.md#changing-the-worker) |
| Add API routes from a package of your own | the `cadless.routers` entry-point group | [below](#adding-routes-and-panels-from-outside-this-tree) |
| Add a panel to the left rail | `registerPanel`, plus `src/plugin.ts` and `src/plugins/` from outside this tree | [below](#adding-routes-and-panels-from-outside-this-tree) |
| Say an item can arrive a new way | `register_origin` | [below](#recording-what-your-build-knows) |
| Remember something about a project | `Store.record_plugin_data` | [below](#recording-what-your-build-knows) |
| Host this for more than one person | `register_principal_resolver` | [below](#saying-who-is-asking) |

Six of these are registries you extend with a single entry, one is a protocol
you satisfy by writing a class, one is a place to keep your own record, and the
rest are data or a directory on disk. None of them requires touching the
pipeline.

## Adding routes and panels from outside this tree

The last two seams are for a build that installs *beside* this one rather than
editing it — the case where you cannot add a line to a list in here because the
tree is not yours to change.

**Backend.** `backend/app.py` includes every router advertised under the
`cadless.routers` entry-point group, after the ones this tree ships. In your
own distribution's `pyproject.toml`:

```toml
[project.entry-points."cadless.routers"]
my-feature = "my_package.routers:router"
```

The value resolves to a FastAPI `APIRouter`. Two things worth knowing:

- **Registration order is registration order.** The built-in routers are
  included first, so an advertised router can add paths but cannot shadow one
  this tree already serves.
- **A router's own `lifespan=` runs.** `include_router` merges it into the
  app's, so an advertised router can do startup housekeeping — sweeping what a
  killed run left staged, warming a pool — without this tree knowing about it.
  No router in this tree passes one today, so the mechanism is stated by
  `backend/app.py` rather than shown by an example: it merges the lifespan and
  wraps it (`_contained`). A failure while starting is logged and contained —
  it costs that router its routes, and the app still boots.

**Frontend.** Same idea, different mechanics — and the difference is the whole
reason it looks the way it does. Python finds an installed distribution at
runtime; a bundle cannot. JavaScript that nothing imports is not in the build,
so `registerPanel` — an ordinary function call — would never run. Discovery has
to happen while the bundle is assembled.

So place the panel under `frontend/src/plugins/<name>/register.tsx`:

```tsx
import { CubeIcon, Panel, registerPanel } from "../../plugin";

registerPanel("my-feature", {
  label: "My Feature",
  icon: <CubeIcon />,
  render: () => <Panel title="My Feature">…</Panel>,
});
```

`frontend/src/panels/plugins.ts` reads that directory with `import.meta.glob`
at build time. Things worth knowing:

- **The directory is git-ignored**, and does not exist in a plain checkout.
  A pattern matching nothing compiles to nothing, so the ordinary build is
  unaffected — that case is pinned by `plugins.test.ts`.
- **Exactly one directory deep.** The pattern is `plugins/*/register.{ts,tsx}`,
  so `plugins/acme/register.tsx` is found and `plugins/@acme/panel/register.tsx`
  is not. Nothing reports the miss — the same "matches nothing is normal" rule
  that makes the empty case work also makes a misplaced panel simply absent, so
  check the depth first when a panel does not appear.
- **Import from `src/plugin.ts`, not from the tree.** That file *is* the
  contract: what it exports is public API, and what it does not is internal and
  will move. It says in its own header what is deliberately withheld and why —
  the 3D viewport store, and the app's state and bootstrap. Read it there rather
  than here; this list has been wrong before, and the file is the thing that
  cannot be.
- **A capability is offered where the handle is withheld.** The pattern is worth
  recognising, because "the store is not exported" does not mean the feature is
  unavailable. Showing a model the machine does not hold yet is `showPreview` /
  `clearPreview` / `usePreviewing` while `viewportStore` stays private; opening
  and cloning a project is `useProjectActions` while `useApp` stays private. So
  if you need something that is not there, ask for the narrowest capability that
  does what you want rather than for the handle behind it — and add it on
  purpose rather than reaching around the contract.
- **The check is mechanical, not editorial.** `.eslintrc.cjs` refuses any import
  from `src/plugins/**` that is not `../../plugin`, so a reach-around fails
  `npm run lint` in the build that has to notice it rather than at some later
  upgrade that broke no promise.
- **Registration order is rail order**, and plugins register after the
  built-ins. Registering an id that is already taken replaces it, so a build can
  substitute a panel as well as add one — and a replacement keeps the slot the
  original held rather than moving to the end, because the registry is a `Map`
  and setting an existing key does not reorder it. So a new id lands at the
  bottom of the rail while an override stays where the panel it replaced was.

## Recording what your build knows

A build that fetches items from somewhere has two things to write down that this
engine has no business understanding: where an item came from, and what it
remembers about a project. Both are seams rather than columns, because a package
installed beside this one cannot add either.

**Where an item came from.** `cadless.catalog.origins` holds
`register_origin(Origin(key, label, sort_order, reader))`. This engine ships
`local` (everything that did not arrive) and `file` (a package handed over
directly); register yours at import, the way a router registers itself:

```python
register_origin(Origin(key="depot", label="Depot", sort_order=10, reader=_read))
```

The `reader` is the part that matters. Given an item's `source.json` and the
licence already read out of it, it returns an `ItemOrigin` if the item is one of
yours and `None` otherwise. **Whatever your fetch writes, your reader reads** —
the sentence, the key, the shape of the ids. Keeping both halves together is the
whole point: an engine holding one of them would be holding a vocabulary for an
arrival it does not implement, and moving one without the other makes every item
received before the move stop being recognised.

Pass what you want recorded through `import_package(..., recorded={"depot":
{...}})`, keyed by your origin. Keys this engine writes itself are refused
rather than silently overwritten — a provenance record must stay what the import
witnessed.

Two things follow from this that are worth knowing before you rely on them:

- **An item recorded by a build that is not installed reads as `unknown`, not
  `file`.** Calling it `file` would claim it was handed over directly, on the
  strength of not recognising it, and the same item would change its story
  depending on which build opened it.
- **`/catalog/origins` answers the keys and labels**, and `/catalog/origins/
  {kind}` answers which items are already held from one of them. The frontend
  reads both rather than keeping its own table, so a registered origin is
  spelled correctly on a card without this tree being edited.

**What you remember about a project.** `Store.record_plugin_data(project_id,
plugin, data)` and `Store.plugin_data(project_id, plugin)` are a slot per build
per project. It replaces rather than merges, keys by your build so two of them
cannot overwrite each other, and goes away with the project. This engine never
reads what you put there.

## Saying who is asking

The engine has no accounts and does not want any. What it has is a way to be
*told* who is asking, so a build hosting it for several people can keep their
work apart while the tool on one machine carries on with nothing configured.

Register a resolver at import — the same moment `register_origin` runs, and for
the same reason:

```python
from cadless.identity import Principal, register_principal_resolver

register_principal_resolver(lambda request: Principal(session_user_id(request)))
```

The argument is the incoming request; what you do with it is entirely yours.
Read a cookie, introspect a bearer token, ask your own service — the engine
never sees any of it. What comes back is a key it compares for equality and
files rows under, plus a label for display.

Things worth knowing before you rely on it:

- **The key must be stable for the same person.** Change it and the work filed
  under the old one becomes invisible rather than reassigned. Use whatever your
  side already treats as permanent.
- **A second registration is refused** unless you pass `replace=True`. This is
  stricter than the other registries on purpose: two add-ons disagreeing about
  who the caller is would answer the security question twice and use whichever
  imported last, and neither would be able to tell it had lost.
- **Keys beginning `cadless:` are refused.** They name the engine's own rows —
  the build's, and the single user of an unhosted build — and a resolver that
  could claim one would read across everybody.
- **A resolver may be async**, for the case where saying who is asking means a
  round trip.
- **Failure is a refusal, not a fallback.** A resolver that raises answers 503
  rather than quietly returning the local user, because a fallback would mean a
  broken sign-in silently reduced the whole installation to one shared identity
  and everybody read everybody else's work while the app answered normally. For
  the same reason, set `CADLESS_REQUIRE_IDENTITY=1` on a hosted deployment: the
  app then refuses to start if no resolver is registered, instead of starting
  with the engine's local default.

**What is scoped, and what is not.** Projects and everything hanging off them —
versions, artifacts, chat, per-build records — are scoped, as is the distilled
knowledge base, because what it returns is quoted to the model as grounding.
Routes reach rows through a per-request view that has no way to widen itself, so
the scoping is not something a route can forget.

Reading the build's rows is not permission to change them — the two rules are
separate, and the write one is narrower by exactly the build's own key. So a
principal cannot rename or delete what every other principal reads, and that
holds for every write rather than for the ones somebody remembered to guard.

A catalogue item is deliberately **not** per-person: both directories the loader
walks are shared, so an item belongs to the installation and is read by
everyone. Customising one is a clone, and the clone is yours.

Three things remain installation-wide and are not this seam's to fix:
`user_settings.json` (one file, so one save changes the model and budget for
everyone), the artifact blob directory, and **who may curate the catalogue**.
A build that stores a credential of its own in that file adds a fourth. Blob *access* is gated in SQL, so the flat
layout leaks nothing by itself — but a build that serves those files by some
other route of its own would bypass the gate.

The last one needs a decision from you if you host untrusted callers.
`POST /packages/import` and `DELETE /catalog/{id}` act for the installation,
because that is what a catalogue item belongs to. This engine has no notion of
privilege and will not invent one, so it cannot tell a caller who may curate
from one who may not — **gate or replace those two routes**, and any curating
route your own build adds, which this seam lets you do without editing this
tree.
[ADR-0006](../adr/0006-identity-seam.md) records the reasoning.

One more thing worth knowing: your resolver's exception text reaches the log.
Do not interpolate the credential you were introspecting into the message.

## Before you start

- **Read the architecture first.** [architecture.md](../architecture.md) maps the
  components and states the layering rules these guides assume — particularly
  that vendor SDKs stay inside `cadless/llm/providers/` and build123d stays
  inside the execution child and the exporters.
- **The decision records explain the why.** These guides tell you what to do;
  [adr/](../adr/) tells you why the seam has the shape it does. Where a guide
  would repeat an ADR, it links instead.
- **You do not need an API key.** The bundled `fake` provider makes the whole
  suite runnable offline, and the catalog CLI's verbs
  paths never call a model.
- **Set up and sign off** per [CONTRIBUTING](../../CONTRIBUTING.md): `make
  install`, then `make test` and `make lint` before opening a pull request, with
  every commit signed off (`git commit -s`).

## If your change does not fit a seam

Some changes genuinely need to reach into the pipeline, the API layer or the
frontend. That is fine — but it is worth opening an issue first to agree the
shape, because those areas have fewer guardrails and a bigger blast radius than
the seams above.
