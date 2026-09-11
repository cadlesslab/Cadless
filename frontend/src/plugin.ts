/** What a panel shipped outside this tree may build against.
 *
 * This file **is** the contract. What it exports is public API: a build that is
 * not in this repository imports from here and nowhere else, so changing any of
 * it breaks that build. What it does not export is internal and may be moved or
 * rewritten freely.
 *
 * One file rather than "import whatever you need from `../..`", because a
 * surface spread across relative imports grows silently — nobody ever decides
 * to freeze `viewportStore`, they just find out later that something depended
 * on it. Here the whole commitment is one screenful, and widening it is a
 * reviewable diff.
 *
 * **It starts deliberately narrow.** An export can be added later; one that has
 * shipped cannot be taken back. Where a plugin turns out to need something that
 * is not here, add it on purpose — do not reach around this file.
 *
 * **An exported type's shape is part of the commitment too.** Adding a required
 * field to one is a breaking change for anything that *builds* a value of it,
 * even though it costs a reader nothing — a plugin's own test fixtures are the
 * case that finds out. Widen one on purpose and say so, the same as adding an
 * export.
 *
 * ## Deliberately not exported
 *
 * - **`viewportStore` / `useViewport`** — driving the 3D viewport. Still
 *   withheld, but what this paragraph asked for now exists: `showPreview`,
 *   `clearPreview` and `usePreviewing` below are the capability, taking the data
 *   and letting the engine decide how to draw it. The store stays in this list
 *   because exporting it would freeze the viewport's internals as public API,
 *   which is far more than the feature needs.
 * - **`useApp`, `Store`, `StoreProvider`, `ToastProvider`** — the app's own
 *   state and bootstrap. A panel is rendered *inside* the app; it does not
 *   assemble one. `useToast` is exported and the provider that backs it is
 *   not, which is the whole distinction: a panel says something, it does not
 *   decide where saying it goes. `useProjectActions` below is the same
 *   distinction applied again: two things a panel can *do*, not the state it
 *   would have had to read to do them.
 * - **The domain calls in `api.ts`** (projects, versions, generation, catalog) —
 *   the named per-endpoint functions, not the module. A plugin talks to its own
 *   routes through `request` below. Reaching into the engine's own endpoints
 *   would make it a second client of surfaces that are free to change.
 *   Two reads are exported anyway, and are named below with the reason: a panel
 *   offering things to bring here has to speak this build's vocabulary for what
 *   a thing is, and know what is already held. Both answers live on the server,
 *   so a plugin that guessed them would disagree with the app beside it.
 */

// The seam itself. `registerPanel` is what puts a panel in the rail; the rail
// draws whatever the registry holds, in registration order.
export { registerPanel, unregisterPanel, type PanelEntry, type PanelId } from "./panels/registry";

// The rail's bottom section, for a control rather than a panel — something that
// is not an icon opening a flyout. A registered control sits above help and the
// theme toggle, which this app keeps to itself.
export {
  registerRailControl,
  unregisterRailControl,
  type RailControlEntry,
  type RailControlId,
} from "./panels/railControls";

// The UI kit the built-in panels are made of, so a plugin's panel looks like
// part of the app rather than a visitor.
//
// Named rather than `export * from "./components"`. The barrel is a deliberate
// list, but it is a list of what *this app* is built from, and re-exporting it
// wholesale would make every later addition to it public API the moment it
// landed — with no diff in this file, which is the one place the commitment is
// supposed to be visible. The two lists can drift; that is what review is for,
// and it is the cheaper failure.
export {
  Button,
  Chips,
  ConfirmDialog,
  EmptyState,
  HelpPopover,
  IconButton,
  ItemCard,
  Modal,
  Panel,
  PromptDialog,
  SegmentedControl,
  Slider,
  TagPicker,
  Textarea,
  TextInput,
  Tooltip,
  useToast,
} from "./components";
export type { Facet, Segment, ToastVariant } from "./components";

// The icons that mean the same thing in any panel. Withheld: `CatalogIcon`,
// `HouseIcon` and `ImportIcon`, which name particular places and controls in
// this app rather than something a panel draws for itself — `ImportIcon` marks
// this build's own rail entry for taking in a package, and a panel that drew it
// would be pointing at a control it does not own; and `CadlessIcon`, which is an
// identity rather than a control — a panel that ships elsewhere brings its own
// mark, and borrowing one from here would put this app's name on it.
//
// The list above is exhaustive against `./components`, and is meant to stay
// that way: an icon added to that barrel and named in neither place is an
// export decision nobody made.
export {
  ChevronDownIcon,
  CloseIcon,
  CubeIcon,
  FolderIcon,
  HelpIcon,
  HistoryIcon,
  InfoIcon,
  MoonIcon,
  SettingsIcon,
  SlidersIcon,
  SunIcon,
} from "./components";

// Where this build's API lives. A plugin needs it to build a URL that is not a
// plain path — an artifact `src`, say, that the browser fetches directly.
export { API_BASE } from "./config";

// The fetch helper the app's own calls go through: it prefixes `API_BASE`,
// sends and parses JSON, and turns a refusal into an `Error` carrying the
// server's message. A plugin calling its own routes wants exactly this, and
// writing its own would be a second answer to "what does a failed request look
// like" — which is what the toast a user sees is made of.
//
// `ApiError` and `errMessage` come with it deliberately. Handing over a
// function while withholding the type it throws is not a narrow contract, it is
// an incomplete one: the caller is left to duck-type `status` off an `unknown`,
// which depends on the same shape with none of the promise. A refusal is only
// half answered by catching it — the other half is telling a status apart (a
// 409 that should ask before overwriting is not a failure to report) and
// turning whatever was thrown into a sentence. Both halves are the same
// contract, so they ship together.
//
// One refusal comes from `request` itself rather than from a server: the path
// must stay rooted at `API_BASE`, because a build contributing a header below
// puts a credential on every call and a path that resolves elsewhere is where
// that credential would go. It is checked by resolving the URL, so a spelling
// that only looks rooted is refused too. That refusal is a plain `Error` and
// not an `ApiError` — nothing was sent, so there is no status to carry.
export { ApiError, request } from "./api";
export { errMessage } from "./errors";

// Adding a header to every request this app makes, rather than only to the ones
// a plugin issues itself. `request` above covers a plugin's own calls; this
// covers the app's, which a build outside this repository cannot reach — they
// are named per endpoint inside `api.ts` and deliberately not exported.
//
// It is exported because the alternative is worse and was measured: with no
// seam here, a composed build's only route to the same effect is patching
// `window.fetch` from a module that happens to evaluate early, which is not a
// contract, breaks the first time `api.ts` is refactored, and leaves this file —
// the one place the commitment is supposed to be visible — silent about it.
// This file's own rule is that what a plugin needs and does not have is added
// here on purpose, so here it is.
//
// A mechanism and no policy: the engine never learns what a contributed header
// means, and this seam names none. **It reaches `fetch` and nothing else** —
// the progress streams are opened with `EventSource`, which carries no custom
// header in any browser, so a contributed header does not reach them.
// `requestHeaders.ts` records both limits in full.
export { registerRequestHeaders, type HeaderContributor } from "./requestHeaders";

// Getting something out of this build, so a panel can put its own way of doing
// that *beside* the ones here rather than instead of them.
//
// The reason it is the component and not a hook: a build that reaches a printer
// this one cannot — over its own network, through something it runs there —
// still wants the formats, the size check and the USB path exactly as they are.
// Withholding `ExportShare` does not prevent that panel; it makes it reimplement
// four behaviours that already had to be reconciled once, and then disagree with
// this build the first time either side changes.
//
// The alternative was a separate panel of its own, needing nothing from here.
// It was rejected because it splits getting-a-file-out from sending-it-somewhere
// back apart, which is the split this build deliberately closed.
//
// **All three names or none.** The component takes a `Version`, so a build with
// no name for that type cannot write the prop; and a panel is mounted by the
// rail with no props at all, so the version has to be *found* rather than
// handed over. Exporting the component alone would be a contract that
// type-checks here and nowhere else — the failure this file exists to prevent.
//
// What this does not widen: the store behind the hook stays withheld, exactly as
// `useApp` and `Store` are above. A panel may ask what is open; it may not
// assemble the state that answers.
export { ExportShare } from "./panels/ExportShare";
export type { Version } from "./api";
export { useActiveVersion } from "./state";

// Showing a model this build does not hold yet. A panel hands over what to draw
// and the engine decides how, which is the difference between a capability and
// the store that backs it — the store is still withheld above.
//
// `usePreviewing` is here because a panel that can start a preview has to be
// able to end one honestly: the viewer's exit control and the panel's own button
// are looking at the same state, and a panel that tracked it separately would
// drift the moment anything else cleared the preview. Opening a project does
// exactly that.
export { clearPreview, showPreview, usePreviewing } from "./viewport/viewportStore";
export type { Preview } from "./viewport/viewportStore";

// What came of putting a package on this machine. A panel that brings work here
// does not perform the import — this build's own importer does, gating the code
// before anything is written and deciding which project it lands in — so the
// answer describes the engine's doing rather than the panel's, and a type of the
// panel's own would be a second name for one event. `project_id` being null is
// the load-bearing part: it means the content was already here unchanged, and a
// panel that read it as a failure would offer a way to open nothing.
export type { ImportResult } from "./api";

// What a panel may do to this machine's own projects, once it has put something
// here: open it, or take an editable copy. Two actions rather than the app, for
// the reason given above — and they raise a toast rather than rejecting, which
// is the app's error convention and not something a panel should re-answer.
export { useProjectActions } from "./useApp";

// This build's vocabulary for what a thing is, and what it already holds. Both
// answers come from the server — `/catalog/domains` and `/catalog/origins/{kind}`
// — so they belong to the deployment rather than to any panel. A panel offering
// things to bring here has to sort them the way the app beside it sorts them,
// and has to know what is already on this machine so it does not offer it twice.
//
// `domainIcon` comes with them. It was withheld on the grounds that it would
// freeze the domain vocabulary as public API, and that turns out to be the wrong
// way round: the keys arrive from `fetchCatalogDomains`, and the function is a
// small map with a fallback rather than a promise to have an icon for every one.
// What exporting it freezes is only that a domain has *an* icon — which is the
// point, because two panels drawing the same domain differently is exactly the
// seam showing.
export { fetchCatalogDomains, fetchHeldOrigins } from "./api";
export type { CatalogDomain, HeldOrigin } from "./api";
export { domainIcon } from "./components";
