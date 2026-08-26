/** Typed client for the Cadless backend (REST + SSE). */
import { API_BASE } from "./config";
import { appendHeader, headerPairs, setHeader } from "./headers";
import { contributedHeaders } from "./requestHeaders";

export interface Project {
  id: number;
  name: string;
  created_at: string;
  updated_at: string;
  current_version_id: number | null;
  // Source version this line was forked from, when branched from a prior turn.
  branched_from_version_id?: number | null;
  // Catalog items are read-only: parameters are shown but editing is gated behind
  // cloning the item into an editable copy.
  is_catalog?: boolean;
  // Customize-from-catalog provenance (#22): clones record the project they were
  // copied from; name + catalog id are resolved server-side so the UI can render
  // a "based on <name>" chip linking back to the catalog item.
  derived_from_project_id?: number | null;
  derived_from_name?: string | null;
  derived_from_catalog_id?: string | null;
}

export type ArtifactKind = "step" | "glb" | "stl" | "obj";

export interface ArtifactRef {
  kind: ArtifactKind;
  bytes: number;
}

export type ParamValue = number | string | boolean;

export interface Version {
  id: number;
  project_id: number;
  prompt: string;
  code: string | null;
  ok: boolean;
  error: string | null;
  volume: number | null;
  bbox: [number, number, number] | null;
  created_at: string;
  parameters: Record<string, ParamValue>;
  parent_version_id: number | null;
  /** UI narration: the active plan step (1-based) this checkpoint was
   * written under, or null when no plan was active. Lets the UI narrate "step N". */
  plan_step: number | null;
  artifacts: ArtifactRef[];
}

export interface GenerateResponse {
  ok: boolean;
  attempt_count: number;
  version: Version;
}

export interface RerunResponse {
  ok: boolean;
  error: string | null;
  version: Version;
}

export type ProgressEvent =
  | { event: "start"; intent: string; max_tries: number; mode?: "generate" | "refine" }
  | { event: "attempt"; n: number; stage: string; ok: boolean; error: string | null }
  // Granular lifecycle. The current panel ignores these; a future
  // staged-progress UI consumes them. phase ∈ interpret|generate|refine|validate|
  // build|mesh|critique|repair, status ∈ begin|ok|error.
  | { event: "stage"; phase: string; status: "begin" | "ok" | "error"; attempt: number; error?: string }
  | { event: "done"; version_id: number; ok: boolean; attempt_count: number }
  | { event: "error"; detail: string };

// ---- block-based transcript (/) ----
export type BlockKind =
  | "text"
  | "thinking"
  | "tool_use"
  | "tool_result"
  | "clarification"
  | "plan";

export interface ContentBlock {
  kind: BlockKind;
  text?: string | null;
  id?: string | null;
  name?: string | null;
  input?: Record<string, unknown> | null;
  tool_use_id?: string | null;
}

export interface MessageOut {
  id: number;
  seq: number;
  role: string;
  content: string | null;
  status: string;
  error: string | null;
  version_id: number | null;
  created_at: string;
  blocks: ContentBlock[];
}

// ---- chat turn SSE events (/) ----
/** UI events emitted by `POST /projects/{id}/chat`. Pipeline `stage` events nest
 * inside `tool_progress`, so the existing StagedProgress is reused verbatim. */
export type ChatEvent =
  | { event: "turn_start" }
  | { event: "text_delta"; text: string }
  | { event: "thinking_delta"; text: string }
  | { event: "tool_start"; tool: string; label: string }
  | { event: "tool_progress"; stage: ProgressEvent }
  // The codegen model's tokens, streamed live as it writes the build123d code
  // during a fresh generate_model.
  | { event: "codegen_delta"; text: string }
  | {
      event: "tool_result";
      version_id: number | null;
      ok: boolean;
      metrics: Record<string, unknown> | null;
      thumbnail: string | null;
      tool: string;
      error: string | null;
    }
  | { event: "clarification"; questions: ClarificationQuestion[] }
  // An ordered plan emitted before the action card for a non-trivial part.
  | { event: "plan"; steps: string[] }
  // A queued/steer message injected mid-run at an iteration boundary.
  | { event: "steer"; text: string }
  | { event: "turn_end"; stop_reason: string | null }
  | { event: "error"; detail: string };

/** One clarifying question with optional quick-reply chips. */
export interface ClarificationQuestion {
  text: string;
  options?: string[];
}

/** The headers one request goes out with: this file's default, then whatever a
 * composed build contributes, then the call's own.
 *
 * The call last, and that order is load-bearing rather than tidy: `importCatalog`
 * below relies on its own content type reaching the server, and a build that
 * could take it away would have that import refused with a 415. A contributor
 * adds to a request; it does not get to re-describe one.
 *
 * `Headers` rather than an object, and that is the whole of how the override is
 * enforced. Header names are case-insensitive, so an object merged by key keeps
 * both `Content-Type` and `content-type` and sends the two values comma-joined
 * under one name — measured as a 422 on this build's own JSON routes, because
 * the body then parses as nothing. `Headers.set` answers to the name rather
 * than to the spelling, so the last writer wins whatever case it used, and a
 * caller's `HeadersInit` is normalised whichever of its three shapes it is
 * (object, `Headers`, array of pairs) including the array's repeated names,
 * which it combines exactly as `fetch` would.
 *
 * The caller's shapes are read here and written one at a time rather than
 * handed to `new Headers(...)`, so that they go in through the same guard the
 * contributed ones do. That constructor validates every value at once and
 * raises the runtime's own `TypeError`, which on at least one engine quotes the
 * value it refused — and `request` is published, so the value in question can
 * be a key a plugin spelled with a stray newline in it.
 */
function outgoingHeaders(init?: RequestInit): Headers {
  const outgoing = new Headers({ "Content-Type": "application/json" });
  for (const [name, value] of Object.entries(contributedHeaders())) {
    setHeader(outgoing, name, value);
  }
  // Repeated names in the array form combine, which is what `new Headers` did
  // here before and what `fetch` does with them — so the caller's headers are
  // gathered first and only then override, rather than the last pair winning.
  const callers = new Headers();
  for (const [name, value] of headerPairs(init?.headers ?? {})) {
    appendHeader(callers, name, value);
  }
  callers.forEach((value, name) => outgoing.set(name, value));
  return outgoing;
}

/** What `req` says when a path would take the request off the API base.
 *
 * One sentence, and it names no part of the caller's input on purpose. It
 * travels out through `errMessage`, which renders `Error.message` straight into
 * a toast — and a caller that got a path wrong is the one most likely to have
 * built it out of something that should not be read aloud.
 */
const UNROOTED = "refusing to send a request: a path must be rooted at the API base";

/** `API_BASE` with a trailing slash off it, which is what everything joins to.
 *
 * Every path in this file brings its own leading slash, so the base must not
 * also end in one. `API_BASE` of `/` is the natural spelling of "the API is at
 * the root", and left alone it turns `/projects` into `//projects` —
 * protocol-relative, origin `projects`, off this site entirely. `/base/` does
 * the milder version of the same thing and asks for `/base//projects`. Every
 * trailing slash comes off rather than one, because `//` trimmed once is still
 * `/` and lands back on the first case.
 *
 * At module scope rather than inside the one function that first needed it,
 * because the streams and the artifact URLs join to the base too and are not
 * routed through that function — a trim in one place would have fixed the
 * guarded calls and left every download and every progress stream pointing at
 * a host named after the first path segment.
 */
export const BASE = API_BASE.replace(/\/+$/, "");

/** Where `path` will actually send the request, refusing it if that is not here.
 *
 * `API_BASE` is a prefix and may be empty, so a `path` that leaves it is a
 * request to somewhere else — and since a composed build may be adding a
 * credential to every call, somewhere else is where that credential would go.
 * Nothing in this file can reach that; the check exists because `request` is
 * published to plugins.
 *
 * **Resolved and compared, never matched as a string**, and that distinction is
 * the whole of it. The caller spells a path and the URL parser reads one, and
 * the two do not agree about what a path is: for a special scheme the parser
 * reads `\` as `/`, and it strips a raw tab, CR or LF before parsing at all. So
 * `/\host/x` and `/<TAB>/host/x` are both protocol-relative once resolved while
 * neither begins with `//` — a prefix test passes them and `fetch` then sends
 * the credential to `host`. Comparing the origin the request is actually going
 * to closes that class however it is spelled, which no test on the input can.
 *
 * Resolving here rather than leaving the string to `fetch` also settles which
 * base applies: this compares against the page's own URL, while a bare string
 * handed to `fetch` is resolved against `document.baseURI`, which a `<base>` tag
 * can move. Checking one and sending the other would be checking nothing.
 *
 * **It rules on where the request is sent, not on where it ends up.** A `3xx`
 * from the API base is followed by `fetch` with the contributed header still
 * attached, and nothing here sees the second hop. The redirects reachable from
 * the API base are Starlette's trailing-slash ones, which stay on it, so
 * closing the hop would trade a hazard nothing here can reach for a refusal a
 * composed build's own routes could hit. A deployment that adds a redirect off
 * the base is choosing that, and owns it.
 */
function apiTarget(path: string): URL {
  let base: URL;
  let target: URL;
  try {
    base = new URL(BASE || "/", window.location.href);
    target = new URL(`${BASE}${path}`, window.location.href);
  } catch {
    throw new Error(UNROOTED);
  }
  // A page with an opaque origin — one served from `file:`, say — reports its
  // origin as the string "null", and so does everywhere else it can reach. The
  // comparison below would hold between two of them and pass the whole class,
  // so refuse before making it rather than compare two nothings.
  if (base.origin === "null") {
    throw new Error(UNROOTED);
  }
  // Rooted, on this origin, and still under the base — the last one both as the
  // parser left it and as a proxy in front of this app would read it.
  if (
    !path.startsWith("/") ||
    target.origin !== base.origin ||
    !under(target.pathname, base.pathname) ||
    !under(asRead(target.pathname), asRead(base.pathname))
  ) {
    throw new Error(UNROOTED);
  }
  return target;
}

/** Whether `target` is the base path itself or something inside it.
 *
 * A base path is a directory, so it is compared as one. `startsWith` on the
 * bare value answers to a sibling that merely begins the same way: under a base
 * of `/apps/cadless/api`, a path resolving to `/apps/cadless/api-admin` is not
 * under it, and on a shared host that is somebody else's app.
 */
function under(target: string, base: string): boolean {
  return target === base || target.startsWith(`${base.replace(/\/$/, "")}/`);
}

/** A path as an intermediary in front of this app would read it.
 *
 * A proxy decodes the percent escapes and resolves the dot segments before it
 * decides which app a request belongs to, and the URL parser does neither for
 * an escaped separator — `..%2f..%2fadmin` stays one segment here and arrives
 * there as `../../admin`. Ruling only on what the parser produced would rule on
 * a path nobody downstream is going to see.
 *
 * Decoding is not enough on its own, because the dot segments it uncovers are
 * still text: the value goes back through the parser to have them resolved, and
 * a `?` or `#` the decode produced is put back escaped first, since either
 * would otherwise end the path and take the rest of it with it.
 */
function asRead(pathname: string): string {
  try {
    const decoded = decodeURIComponent(pathname);
    return new URL(decoded.replace(/[?#]/g, (c) => encodeURIComponent(c)), "http://read.invalid")
      .pathname;
  } catch {
    // Both steps are inside, and the parse is not the formality it looks like:
    // a decode that yields a leading `//` puts the parser into the authority
    // state, where a character no host may carry throws. Outside the `try` that
    // throw is the runtime's own, and its message quotes the decoded path —
    // which is the caller's input, on a screen, in a refusal whose whole point
    // is naming none of it.
    throw new Error(UNROOTED);
  }
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(apiTarget(path).toString(), {
    ...init,
    headers: outgoingHeaders(init),
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail);
  }
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

/** `req`, under the name the plugin contract publishes it as.
 *
 * Aliased rather than renamed: every call in this file reads `req(...)` and
 * always has, while a panel shipped from outside this tree is better served by
 * a name that says what it is. `src/plugin.ts` re-exports this one and nothing
 * else from here.
 */
export { req as request };

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

// ---- projects ----
export const listProjects = () => req<Project[]>("/projects");
export const createProject = (name: string) =>
  req<Project>("/projects", { method: "POST", body: JSON.stringify({ name }) });
export const getProject = (id: number) => req<Project>(`/projects/${id}`);
export const renameProject = (id: number, name: string) =>
  req<Project>(`/projects/${id}`, { method: "PATCH", body: JSON.stringify({ name }) });
export const deleteProject = (id: number) =>
  req<void>(`/projects/${id}`, { method: "DELETE" });
/** Fork a prior version into a brand-new project/line, returning it. The
 * new line is seeded from the selected version's model; the original is unchanged. */
export const branchFromVersion = (projectId: number, versionId: number, name?: string) =>
  req<Project>(`/projects/${projectId}/branch`, {
    method: "POST",
    body: JSON.stringify(name ? { version_id: versionId, name } : { version_id: versionId }),
  });

/** Deep-clone a whole project (full chat history + every version's code/artifacts)
 * into a new editable project. Used by the catalog Clone action. */
export const cloneProject = (projectId: number, name?: string) =>
  req<Project>(`/projects/${projectId}/clone`, {
    method: "POST",
    body: JSON.stringify(name ? { name } : {}),
  });

// ---- catalog ----
export interface CatalogItem {
  house_id: string;
  name: string;
  project_id: number;
  current_version_id: number | null;
  steps: number;
  domain: string;
  // Discovery metadata (#21); nullable/empty on items without it.
  category: string | null;
  tags: string[];
  description: string | null;
  /** API path of the baked thumbnail PNG (prefix with API_BASE), or null. */
  thumbnail_url: string | null;
  /** Whether the app can take this item off this machine: it arrived here as a
   * package, or nothing on disk claims it any more and the record is all there
   * is to remove. False for an item in the catalog loaded at startup, which
   * would be back at the next one, so the server refuses. */
  removable: boolean;
  /** Set when the item's files are gone. Removing it takes the record and
   * nothing else, which is a different thing to confirm. */
  files_missing: boolean;
  /** Where the copy came from, as the item's own provenance records it — the
   * key of one of the origins `/catalog/origins` answers with.
   * `local` is everything that did not arrive here — the bundled samples and
   * anything authored on this machine, which the tool does not tell apart.
   * `null` means the item did not say, which is none of the answers, and is
   * also what an item whose files are gone gets: it is absent from the walk
   * that answers this, and calling that local would be a claim.
   *
   * A string rather than the three this build happens to ship. A closed union
   * would be a second copy of the server's registry, and the first thing a
   * build adding a way of arriving would discover is that its own items do not
   * type-check. */
  source: string | null;
}

export interface CatalogGroup {
  domain: string;
  label: string;
  items: CatalogItem[];
}

/** One filter chip: a domain or category with its item count (#21). */
export interface CatalogFacet {
  key: string;
  label: string;
  count: number;
}

export interface CatalogQuery {
  /** Case-insensitive search over name, tags, and description. */
  q?: string;
  domain?: string;
  category?: string;
  source?: string;
  limit?: number;
  offset?: number;
}

export interface CatalogResponse {
  /** Legacy grouped view of the returned page (pre-#21 shape). */
  groups: CatalogGroup[];
  /** The returned page, flat, in stable domain-then-name order. */
  items: CatalogItem[];
  /** Matches after filtering, before pagination. */
  total: number;
  limit: number;
  offset: number;
  domains: CatalogFacet[];
  categories: CatalogFacet[];
  /** Where the items came from, counted over the whole catalog like domains.
   * Only the answers items actually carry appear — one that did not say is not
   * gathered under a name it never took. */
  sources: CatalogFacet[];
  /** The item details (tags, categories, thumbnails, step counts) could not be
   * read, so this listing is names only. The items themselves are all here. */
  details_unavailable?: boolean;
}

/** One item this machine already holds a copy of, from a given origin. */
export interface HeldOrigin {
  house_id: string;
  catalog_id: string;
  version_id: string | null;
  digest: string | null;
}

/** The curated catalog of loaded benchmark projects — searchable, filterable,
 * and paginated (#21). */
export const fetchCatalog = (query: CatalogQuery = {}) => {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== "") params.set(key, String(value));
  }
  const qs = params.toString();
  return req<CatalogResponse>(`/catalog${qs ? `?${qs}` : ""}`);
};

/** One domain this build knows about, as something to offer rather than
 * something counted. A `CatalogFacet` says what is here; this says what can be
 * asked for, which is the answer a panel browsing somewhere else needs — that
 * listing holds what other people published, and nothing local has to exist
 * for a domain to be worth narrowing by. */
export interface CatalogDomain {
  key: string;
  label: string;
}

export const fetchCatalogDomains = () =>
  req<{ domains: CatalogDomain[] }>("/catalog/domains");

/** One way an item can have arrived, as this build presents it.
 *
 * The same shape as `CatalogDomain` and for the same reason: the labels and
 * their order live on the server, so a build that ships another way of arriving
 * is spelled correctly here without this file being edited. A second copy of
 * that table in the frontend would disagree with the first the moment one was
 * added. */
export interface CatalogOrigin {
  key: string;
  label: string;
}

export const fetchCatalogOrigins = () => req<{ origins: CatalogOrigin[] }>("/catalog/origins");

/** Every item already held here that came from one origin, all of them at once.
 *
 * Not a filter on the listing above: marking a page of search results needs the
 * whole set, and reading a paginated catalog for it would either page through
 * everything or quietly mark only the first window. */
export const fetchHeldOrigins = (kind: string) =>
  req<{ items: HeldOrigin[] }>(`/catalog/origins/${encodeURIComponent(kind)}`);

/** Take a catalog item off this machine — whatever is left of it here.
 *
 * A received item goes in full: its project, its ledger entry and its files.
 * Deleting the project on its own is refused, and would have left the entry
 * behind: enough to make the item skip its next load and to refuse a fresh
 * import of the same package.
 *
 * Three answers, matching `removable` and `files_missing` above. 204 for a
 * received item and for a record whose files are gone (that one takes the
 * record only). 403 for an item in the catalog the app loads at startup, which
 * would be back at the next one. 503 when the app could not read a catalog root
 * and so cannot tell which of those it is — nothing was removed, and trying
 * again once the catalog is readable is the answer. */
export const removeCatalogItem = (houseId: string) =>
  req<void>(`/catalog/${encodeURIComponent(houseId)}`, { method: "DELETE" });

export interface ReparametrizeResponse {
  ok: boolean;
  error: string | null;
  version: Version;
}

// ---- generation / versions ----
export const generate = (projectId: number, prompt: string) =>
  req<GenerateResponse>(`/projects/${projectId}/generate`, {
    method: "POST",
    body: JSON.stringify({ prompt }),
  });
/** Diff-style refinement: edit a prior version with a delta instruction. */
export const refine = (projectId: number, priorVersionId: number, deltaPrompt: string) =>
  req<GenerateResponse>(`/projects/${projectId}/generate`, {
    method: "POST",
    body: JSON.stringify({ prior_version_id: priorVersionId, delta_prompt: deltaPrompt }),
  });
/** Deterministic re-run with overridden parameters, no LLM call. */
export const reparametrize = (versionId: number, params: Record<string, ParamValue>) =>
  req<ReparametrizeResponse>(`/versions/${versionId}/reparametrize`, {
    method: "POST",
    body: JSON.stringify({ params }),
  });
export const listVersions = (projectId: number) =>
  req<Version[]>(`/projects/${projectId}/versions`);
export const getVersion = (id: number) => req<Version>(`/versions/${id}`);

/** Runtime settings. GET returns a masked snapshot — key values are never sent to the
 * client, only whether each key is set and where it came from. */
export interface SecretStatus {
  set: boolean;
  source: "env" | "saved" | "unset";
}

/** Engine tuning knobs, reported with where each value came from.
 *
 * Spelled out rather than left to an index signature: the panel renders them
 * from a table, but a mistyped field name here would then be a runtime blank
 * instead of a compile error.
 */
export interface TuningKnobs {
  rag_top_k?: number;
  rag_top_k_source?: string;
  rag_similarity_floor?: number;
  rag_similarity_floor_source?: string;
  rag_success_weight?: number;
  rag_success_weight_source?: string;
  rag_require_tag_overlap?: boolean;
  rag_require_tag_overlap_source?: string;
  bedrock_temperature?: number;
  bedrock_temperature_source?: string;
  forge_temperature?: number;
  forge_temperature_source?: string;
  vlm_model_slug?: string;
  vlm_model_slug_source?: string;
  bedrock_model_slug?: string;
  bedrock_model_slug_source?: string;
  bedrock_fast_model_slug?: string;
  bedrock_fast_model_slug_source?: string;
}

export interface SettingsStatus extends TuningKnobs {
  providers: string[];
  provider: string;
  provider_source: string;
  orchestrator_model: string;
  orchestrator_model_source: string;
  codegen_model: string;
  codegen_model_source: string;
  aws_region: string;
  aws_region_source: string;
  /** Where the 3D printer is, or null when none has been set. Saved state
   * rather than configuration, so unlike the fields above it has no `_source`:
   * there is only one place it can have come from. */
  printer_address: string | null;
  /** What the printer is, as against where it is. Null for anything the user
   * has not said, and the engine keeps its own default for each of those, so a
   * panel nobody has opened changes nothing about how a model is sliced. */
  printer_bed_width: number | null;
  printer_bed_depth: number | null;
  printer_max_height: number | null;
  printer_nozzle_diameter: number | null;
  printer_filament_diameter: number | null;
  printer_filament_density: number | null;
  printer_cartridge_grams: number | null;
  printer_nozzle_temperature: number | null;
  printer_bed_temperature: number | null;
  secrets: Record<string, SecretStatus>;
}

export interface SettingsUpdate {
  provider?: string;
  orchestrator_model?: string;
  codegen_model?: string;
  aws_region?: string;
  anthropic_api_key?: string;
  openai_api_key?: string;
  aws_access_key_id?: string;
  aws_secret_access_key?: string;
  aws_session_token?: string;
  rag_top_k?: number;
  rag_similarity_floor?: number;
  rag_success_weight?: number;
  rag_require_tag_overlap?: boolean;
  bedrock_temperature?: number;
  forge_temperature?: number;
  vlm_model_slug?: string;
  bedrock_model_slug?: string;
  bedrock_fast_model_slug?: string;
  printer_address?: string;
  printer_bed_width?: number;
  printer_bed_depth?: number;
  printer_max_height?: number;
  printer_nozzle_diameter?: number;
  printer_filament_diameter?: number;
  printer_filament_density?: number;
  printer_cartridge_grams?: number;
  printer_nozzle_temperature?: number;
  printer_bed_temperature?: number;
}

/** What came of taking a received `.cls` into the catalog on this machine. */
export interface ImportResult {
  id: string;
  name: string;
  digest: string;
  /** Whether a fingerprint was offered to check this copy against. False is not
   * a doubt about the package — it is that there was nothing to compare it
   * with, and the two must never be shown as the same thing. */
  digest_confirmed: boolean;
  /** How many steps the code gate cleared. It runs before anything is written:
   * this machine is where that code would run. */
  steps_checked: number;
  /** The project the item was loaded into, or null when it was already here and
   * unchanged. */
  project_id: number | null;
}

export const getSettings = () => req<SettingsStatus>("/settings");
export const saveSettings = (patch: SettingsUpdate) =>
  req<SettingsStatus>("/settings", { method: "POST", body: JSON.stringify(patch) });

/** What this installation can currently do about printing.
 *
 * Asked before a print is attempted so the UI can say what is missing up front.
 * A Print button that explains it has no address beats one that fails after
 * spending two minutes slicing.
 */
export interface PrintCapability {
  slicer_available: boolean;
  slicer_path: string;
  /** What to install, when there is nothing to slice with. Empty otherwise. */
  slicer_hint: string;
  printer_configured: boolean;
  /** Whether an address can be recorded here at all. Without it, "none yet" on
   * a laptop and "none is possible" on a hosted build look identical, and one
   * of those readers has something to go and do. */
  can_configure: boolean;
  /** `auto` | `download` | `off` — the operator's switch, for display only.
   * What to draw is `can_send` / `can_download`, which already account for it. */
  mode: string;
  /** Whether this deployment can reach a printer at all. False on anything in a
   * datacentre, which has no route to the network the printer is on. */
  can_send: boolean;
  /** Whether the sliced job can be handed back as a file. True wherever there
   * is a slicer — it is the half that works from everywhere. */
  can_download: boolean;
}

/** What slicing produced: the numbers someone wants before committing filament. */
export interface SliceResult {
  ok: boolean;
  detail: string;
  /** Separate from `ok` because the answer is an install, not a retry. */
  slicer_missing: boolean;
  stats: Record<string, string>;
}

/** What came of putting a job on the wire. */
export interface PrintResult {
  ok: boolean;
  detail: string;
  /** The failure kind — `address`, `refused`, `timeout`, `unreachable`, `empty`
   * — so the UI can choose a sentence without matching on prose. */
  reason: string;
  bytes_sent: number;
}

export interface PrinterTest {
  ok: boolean;
  detail: string;
  reason: string;
  status: Record<string, unknown>;
  status_detail: string;
}

/** What every printing call sends, and why it is not decoration.
 *
 * These routes take no body, and a body-less request is a CORS "simple
 * request": the browser sends it cross-site without asking, so the server's
 * origin allowlist never gets consulted. A custom header is exactly what makes
 * it non-simple — the browser must preflight, the allowlist answers, and a page
 * that is not this app is refused before it can start a print on someone's
 * machine. The server refuses a request that arrives without it.
 */
const PRINT_HEADERS = { "X-Cadless-Action": "1" };

export const fetchPrintCapability = () =>
  req<PrintCapability>("/printing/capability", { headers: PRINT_HEADERS });

/** Open and close the printer's job port. Prints nothing.
 *
 * Takes an address so the Settings panel can check a value the user has typed
 * but not yet saved; omitting it tests the saved one.
 */
export const testPrinter = (address?: string) =>
  req<PrinterTest>("/printing/test", {
    method: "POST",
    headers: PRINT_HEADERS,
    body: JSON.stringify({ address: address ?? null }),
  });

/** Where the sliced job can be fetched from.
 *
 * Not a plain link: the route carries the same action header as the rest of
 * this group, and an `<a href>` cannot attach one. `ExportShare` fetches it and
 * saves the blob, the way it already does for the export formats.
 */
/** What the machine says it has left.
 *
 * Its own figure rather than a tally kept here: a count of what this tool has
 * printed would drift the moment somebody printed from the panel or changed the
 * cartridge.
 *
 * `ok: false` is an ordinary answer — no address, printer off, no cartridge —
 * and never a reason not to offer the print.
 */
export interface FilamentLevel {
  ok: boolean;
  detail: string;
  /** How full the cartridge is, or null when the machine will not say. */
  percent: number | null;
  loaded?: boolean;
  colour?: string | null;
  /** The same figure in grams, and only when somebody has said how much a full
   * cartridge holds. The printer reports a proportion and never says of what. */
  grams_left?: number | null;
  cartridge_grams?: number | null;
}

export const fetchFilamentLevel = () =>
  req<FilamentLevel>("/printing/filament", { headers: PRINT_HEADERS });

export const gcodeUrl = (versionId: number) =>
  `${BASE}/printing/versions/${versionId}/gcode`;

/** The headers a fetch of that URL needs. Exported so the caller does not have
 * to know the header's name to download a file. */
export const printHeaders = (): Record<string, string> => ({ ...PRINT_HEADERS });

/** Forget the saved address. Its own call because saving only ever sets, so an
 * emptied box cannot mean "remove this". */
export const forgetPrinterAddress = () =>
  req<{ ok: boolean }>("/printing/address", { method: "DELETE", headers: PRINT_HEADERS });

/** Forget every saved measurement, returning the profile to its defaults.
 *
 * Saving cannot do this: that endpoint only ever sets, so a blank box means
 * "leave it alone" -- right for a value nobody retyped, and no way back for one
 * they got wrong. */
export const forgetPrinterProfile = () =>
  req<{ ok: boolean }>("/printing/profile", { method: "DELETE", headers: PRINT_HEADERS });

export const sliceVersion = (versionId: number) =>
  req<SliceResult>(`/printing/versions/${versionId}/slice`, {
    method: "POST",
    headers: PRINT_HEADERS,
  });

export const sendVersionToPrinter = (versionId: number) =>
  req<PrintResult>(`/printing/versions/${versionId}/send`, {
    method: "POST",
    headers: PRINT_HEADERS,
  });
/** Take a `.cls` already on this machine into the catalog.
 *
 * The file is the request body rather than a form field: there is exactly one,
 * and the server carries no multipart parser. `expectedDigest` is what the
 * sender says the package hashes to — the only thing that catches an edit made
 * after they let go of it. */
export const importCatalog = (file: File, expectedDigest?: string) => {
  const query = new URLSearchParams({ filename: file.name });
  if (expectedDigest) query.set("expected_digest", expectedDigest);
  return req<ImportResult>(`/packages/import?${query}`, {
    method: "POST",
    // Required, not decoration: the server refuses every content type a form
    // could have sent, which is what stops another site posting a package here.
    headers: { "Content-Type": "application/octet-stream" },
    body: file,
  });
};
export const rerunVersion = (id: number) =>
  req<RerunResponse>(`/versions/${id}/rerun`, { method: "POST" });
export const setCurrent = (projectId: number, versionId: number) =>
  req<{ current_version_id: number }>(`/projects/${projectId}/current`, {
    method: "POST",
    body: JSON.stringify({ version_id: versionId }),
  });

// ---- artifact URLs ----
export const artifactUrl = (versionId: number, kind: ArtifactKind) =>
  `${BASE}/versions/${versionId}/artifacts/${kind}`;
export const stepUrl = (versionId: number) => artifactUrl(versionId, "step");
export const glbUrl = (versionId: number) => artifactUrl(versionId, "glb");

// ---- SSE generation stream ----
export interface StreamHandle {
  close: () => void;
}

/** Open an SSE generation stream; calls onEvent for each progress event.
 *
 * **No contributed header reaches these two routes.** `EventSource` takes a URL
 * and nothing else — there is no header argument in any browser — so a build
 * composed with `registerRequestHeaders` sees its headers on every `fetch` here
 * and on neither of the streams. Two things follow. A deployment that gates the
 * spending routes on a header gates `POST /chat` and not these; and the
 * reachable-looking workaround, putting the value in the query string, is not
 * one — it would write whatever the header carries into every access log
 * between here and the server. Covering these needs a different transport, not
 * a different registry.
 */
function openStream(
  query: string,
  onEvent: (e: ProgressEvent) => void,
  onError?: (err: Event) => void,
): StreamHandle {
  const es = new EventSource(`${BASE}/projects/${query}`);
  es.onmessage = (msg) => {
    const data = JSON.parse(msg.data) as ProgressEvent;
    onEvent(data);
    if (data.event === "done" || data.event === "error") es.close();
  };
  es.onerror = (err) => onError?.(err);
  return { close: () => es.close() };
}

export function streamGenerate(
  projectId: number,
  prompt: string,
  onEvent: (e: ProgressEvent) => void,
  onError?: (err: Event) => void,
): StreamHandle {
  return openStream(
    `${projectId}/generate/stream?prompt=${encodeURIComponent(prompt)}`,
    onEvent,
    onError,
  );
}

// ---- block-based transcript + chat turn ----
/** Block-based transcript for a project. */
export const getMessages = (projectId: number) =>
  req<MessageOut[]>(`/projects/${projectId}/messages`);

/** Drive a `POST /projects/{id}/chat` SSE turn, calling `onEvent` per parsed UI
 * event. Resolves when the stream ends or is aborted via `signal` (Stop). Unlike
 * the legacy generation streams this is a POST with a JSON body, so it uses fetch
 * + a streaming reader rather than EventSource. */
export async function streamChat(
  projectId: number,
  message: string,
  onEvent: (e: ChatEvent) => void,
  signal?: AbortSignal,
  forge = false,
): Promise<void> {
  let res: Response;
  try {
    // Through the same resolver as `req`, for the same reason the headers go
    // through the same helper. This is the route that spends, so it is the one
    // carrying a contributed credential that matters most — and handing `fetch`
    // a bare string would have resolved it against `document.baseURI`, which a
    // `<base>` tag can move, while the guard next door reads the page's own
    // URL. Checking one and sending the other would be checking nothing.
    res = await fetch(apiTarget(`/projects/${projectId}/chat`).toString(), {
      method: "POST",
      // Through the same helper as `req`, not a second literal: this call does
      // not go through `req` at all, and a build's contributed headers reaching
      // every route except the one that spends is the failure that looks like
      // it works. Its content type goes in as this call's own rather than left
      // to the helper's default, so a contributor cannot take it away — the
      // default is overridable by design and there is no `init` here to reassert
      // it from, which is the difference between this call and `importCatalog`.
      headers: outgoingHeaders({ headers: { "Content-Type": "application/json" } }),
      // `forge` opts this turn into best-of-N racing. It only takes
      // effect if the server's global forge kill-switch is also on (both-true gate).
      body: JSON.stringify({ message, forge }),
      signal,
    });
  } catch (err) {
    if (signal?.aborted || (err as Error)?.name === "AbortError") return;
    throw err;
  }
  if (!res.ok || !res.body) {
    if (signal?.aborted) return;
    throw new ApiError(res.status, res.statusText);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      // SSE frames are separated by a blank line; each frame's `data:` lines join.
      let sep: number;
      while ((sep = buffer.indexOf("\n\n")) !== -1) {
        const frame = buffer.slice(0, sep);
        buffer = buffer.slice(sep + 2);
        const data = frame
          .split("\n")
          .filter((l) => l.startsWith("data:"))
          .map((l) => l.slice(5).trim())
          .join("");
        if (data) onEvent(JSON.parse(data) as ChatEvent);
      }
    }
  } catch (err) {
    if (signal?.aborted || (err as Error)?.name === "AbortError") return;
    throw err;
  } finally {
    reader.releaseLock?.();
  }
}

/** Queue a steer message for the project's in-flight `/chat` turn. The
 * running agent loop drains it at its next iteration boundary, injecting it so the
 * next model call sees it. Returns 202 (accepted/queued). */
export const steerChat = (projectId: number, message: string) =>
  req<{ queued: boolean }>(`/projects/${projectId}/chat/steer`, {
    method: "POST",
    body: JSON.stringify({ message }),
  });

/** SSE refinement stream: prior version + delta instruction. */
export function streamRefine(
  projectId: number,
  priorVersionId: number,
  deltaPrompt: string,
  onEvent: (e: ProgressEvent) => void,
  onError?: (err: Event) => void,
): StreamHandle {
  const q = `${projectId}/generate/stream?prior_version_id=${priorVersionId}&delta_prompt=${encodeURIComponent(
    deltaPrompt,
  )}`;
  return openStream(q, onEvent, onError);
}
