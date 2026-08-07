/** Typed client for the Cadless backend (REST + SSE). */
import { API_BASE } from "./config";

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

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
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
  `${API_BASE}/versions/${versionId}/artifacts/${kind}`;
export const stepUrl = (versionId: number) => artifactUrl(versionId, "step");
export const glbUrl = (versionId: number) => artifactUrl(versionId, "glb");

// ---- SSE generation stream ----
export interface StreamHandle {
  close: () => void;
}

/** Open an SSE generation stream; calls onEvent for each progress event. */
function openStream(
  query: string,
  onEvent: (e: ProgressEvent) => void,
  onError?: (err: Event) => void,
): StreamHandle {
  const es = new EventSource(`${API_BASE}/projects/${query}`);
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
    res = await fetch(`${API_BASE}/projects/${projectId}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
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
