/** App actions: orchestrate API calls + store updates.
 *
 * Pure-ish: each takes the Store and performs an effect. Viewport loading is NOT
 * here — the Viewport component reacts to the active version. Errors propagate to
 * callers, which surface them as toasts.
 *
 * The one thing they do say to the viewport is when to stop showing something
 * that is not on this machine. Every way back to your own work runs through
 * here — opening a project, picking a version, versions arriving as a
 * generation finishes — so this is where a preview ends, rather
 * than in an effect the viewport would have to keep watching for.
 */
import * as api from "./api";
import type { Store } from "./store";
import { viewportStore } from "./viewport/viewportStore";

export async function refreshProjects(store: Store): Promise<void> {
  store.set({ projects: await api.listProjects() });
}

export async function loadVersions(store: Store, projectId: number): Promise<void> {
  viewportStore.clearPreview();
  const versions = await api.listVersions(projectId);
  const project = store.get().projects.find((p) => p.id === projectId);
  const activeId = project?.current_version_id ?? versions.at(-1)?.id ?? null;
  store.set({ versions, activeVersionId: activeId });
}

export function showVersion(store: Store, versionId: number): void {
  viewportStore.clearPreview();
  store.set({ activeVersionId: versionId });
}

export async function loadMessages(store: Store, projectId: number): Promise<void> {
  store.set({ messages: await api.getMessages(projectId) });
}

export async function selectProject(store: Store, id: number): Promise<void> {
  viewportStore.clearPreview();
  store.set({
    activeProjectId: id,
    versions: [],
    activeVersionId: null,
    events: [],
    messages: [],
    chatEvents: [],
    chatPending: null,
  });
  await Promise.all([loadVersions(store, id), loadMessages(store, id)]);
}

export async function createProject(store: Store, name: string): Promise<void> {
  const p = await api.createProject(name);
  await refreshProjects(store);
  await selectProject(store, p.id);
}

export async function renameProject(store: Store, id: number, name: string): Promise<void> {
  await api.renameProject(id, name);
  await refreshProjects(store);
}

export async function removeProject(store: Store, id: number): Promise<void> {
  await api.deleteProject(id);
  if (store.get().activeProjectId === id) {
    store.set({ activeProjectId: null, versions: [], activeVersionId: null });
  }
  await refreshProjects(store);
  const next = store.get().projects.at(-1);
  if (store.get().activeProjectId == null && next) await selectProject(store, next.id);
}

/** Fork a prior version into a new project/line and switch to it. The
 * original project is left untouched; the new line starts from the selected
 * version's model. */
export async function branchFrom(store: Store, versionId: number): Promise<void> {
  const pid = store.get().activeProjectId;
  if (pid == null) return;
  const branched = await api.branchFromVersion(pid, versionId);
  await refreshProjects(store);
  await selectProject(store, branched.id);
}

/** Clone a catalog item into a brand-new, editable project and switch to it.
 * Deep-copies the whole project (full chat history + every version's code and
 * artifacts), so the copy opens fully populated; the catalog original is
 * untouched. */
export async function cloneCatalogItem(
  store: Store,
  projectId: number,
  name?: string,
): Promise<void> {
  const cloned = await api.cloneProject(projectId, name);
  await refreshProjects(store);
  await selectProject(store, cloned.id);
}

/** Take a received catalog item off this machine. The project goes with it, so
 * stop showing it first; the panel refetches the catalog itself. Nothing is
 * opened in its place — the user is browsing the catalog, not the project they
 * were last in, and that is also why the transcript has to be cleared here:
 * `ChatPanel` renders `messages` whether or not a project is selected, so
 * leaving them would show the removed item's chat under an empty workspace. */
export async function removeCatalogItem(
  store: Store,
  houseId: string,
  projectId: number,
): Promise<void> {
  await api.removeCatalogItem(houseId);
  if (store.get().activeProjectId === projectId) {
    store.set({
      activeProjectId: null,
      versions: [],
      activeVersionId: null,
      messages: [],
      events: [],
      chatEvents: [],
      chatPending: null,
    });
  }
  await refreshProjects(store);
}

export async function rerunVersion(store: Store, id: number): Promise<void> {
  await api.rerunVersion(id);
  const pid = store.get().activeProjectId;
  if (pid != null) await loadVersions(store, pid);
}

/** Deterministic parametric re-run (no LLM): persists a new version, selects it. */
export async function reparametrize(
  store: Store,
  versionId: number,
  params: Record<string, api.ParamValue>,
): Promise<void> {
  await api.reparametrize(versionId, params);
  const pid = store.get().activeProjectId;
  if (pid != null) {
    await refreshProjects(store);
    await loadVersions(store, pid);
  }
}

/** Common SSE wiring for both fresh generation and refinement. */
function runStream(store: Store, open: (onEvent: (e: api.ProgressEvent) => void) => api.StreamHandle): void {
  store.set({ generating: true, events: [] });
  open((e) => {
    store.set({ events: [...store.get().events, e] });
    if (e.event === "done") {
      store.set({ generating: false });
      const pid = store.get().activeProjectId;
      if (pid != null) void refreshProjects(store).then(() => loadVersions(store, pid));
    } else if (e.event === "error") {
      store.set({ generating: false });
    }
  });
}

export function generate(store: Store, prompt: string): void {
  const pid = store.get().activeProjectId;
  if (pid == null) return;
  runStream(store, (onEvent) => api.streamGenerate(pid, prompt, onEvent));
}

/** Refine the given prior version with a delta instruction (/). */
export function refine(store: Store, priorVersionId: number, deltaPrompt: string): void {
  const pid = store.get().activeProjectId;
  if (pid == null) return;
  runStream(store, (onEvent) => api.streamRefine(pid, priorVersionId, deltaPrompt, onEvent));
}

/** Drive a `POST /chat` SSE turn: stream UI events into the store and
 * refresh the transcript + versions once the turn settles. A Stop handle is held
 * in `abortChat` so the composer can abort the in-flight turn. */
export function chat(store: Store, message: string, forge = false): void {
  const pid = store.get().activeProjectId;
  if (pid == null || store.get().generating) return;
  const controller = new AbortController();
  store.set({
    generating: true,
    chatEvents: [],
    chatPending: message,
    abortChat: () => controller.abort(),
  });

  const settle = async () => {
    store.set({ generating: false, chatPending: null, abortChat: null });
    await refreshProjects(store);
    await Promise.all([loadVersions(store, pid), loadMessages(store, pid)]);
    // On a clean turn the persisted transcript now carries it; drop the live
    // events so it isn't rendered twice. On an error/abort, keep the live turn so
    // its stopped state + Retry stays visible until the user acts.
    const errored = store.get().chatEvents.some((e) => e.event === "error");
    if (!errored) store.set({ chatEvents: [] });
  };

  void api
    .streamChat(
      pid,
      message,
      (e) => store.set({ chatEvents: [...store.get().chatEvents, e] }),
      controller.signal,
      forge,
    )
    .catch((e: unknown) => {
      const detail = e instanceof Error ? e.message : "chat failed";
      store.set({ chatEvents: [...store.get().chatEvents, { event: "error", detail }] });
    })
    .finally(() => void settle());
}

/** Abort the in-flight chat turn (Stop button). */
export function stopChat(store: Store): void {
  store.get().abortChat?.();
}

/** Queue a steer message for the in-flight chat turn: post it to the
 * session so the running agent loop injects it at its next iteration boundary.
 * A no-op when no turn is generating; the queued message renders in the transcript
 * via the turn's `steer` SSE event once the loop consumes it. */
export async function steerChat(store: Store, message: string): Promise<void> {
  const pid = store.get().activeProjectId;
  const text = message.trim();
  if (pid == null || !text || !store.get().generating) return;
  await api.steerChat(pid, text);
}

/** Open a specific project (deep link / share link). ``versionId`` is optional:
 * a bare ``/apps/cadless/<id>`` opens the project at its current version, while a
 * share link also pins a version. Unknown project → normal
 * startup. */
export async function openShared(
  store: Store, projectId: number, versionId?: number,
): Promise<void> {
  await refreshProjects(store);
  if (!store.get().projects.some((p) => p.id === projectId)) {
    return bootstrap(store); // unknown project — fall back to normal startup
  }
  await selectProject(store, projectId);
  if (versionId != null && store.get().versions.some((v) => v.id === versionId)) {
    showVersion(store, versionId);
  }
}

export async function bootstrap(store: Store): Promise<void> {
  await refreshProjects(store);
  if (store.get().projects.length === 0) {
    await api.createProject("Untitled project");
    await refreshProjects(store);
  }
  const { projects, activeProjectId } = store.get();
  if (activeProjectId == null && projects.length > 0) {
    await selectProject(store, projects[projects.length - 1].id);
  }
}
