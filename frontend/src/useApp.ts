/** Bound app actions with toast-on-error. */
import * as actions from "./actions";
import { useToast } from "./components";
import { useStore } from "./state";

/** The two actions a panel shipped outside this tree is given.
 *
 * `plugin.ts` withholds `useApp` — a panel is rendered *inside* the app and does
 * not assemble one — but a panel that puts a catalogue item on this machine has
 * to be able to open what it just made, or the user is left holding a result
 * with nowhere to go.
 *
 * Narrowed from `useApp` rather than rebuilt beside it, so the two cannot drift.
 * That also carries `guard` across without restating it: these fail by raising a
 * toast, never by rejecting, and a panel written against a rejecting promise
 * would silently never see its catch. */
export function useProjectActions(): Pick<
  ReturnType<typeof useApp>,
  "selectProject" | "cloneCatalogItem"
> {
  const { selectProject, cloneCatalogItem } = useApp();
  return { selectProject, cloneCatalogItem };
}

export function useApp() {
  const store = useStore();
  const toast = useToast();

  function guard<A extends unknown[]>(fn: (...a: A) => Promise<void> | void) {
    return async (...args: A) => {
      try {
        await fn(...args);
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        toast.error("Action failed", msg);
      }
    };
  }

  return {
    store,
    createProject: guard((name: string) => actions.createProject(store, name)),
    renameProject: guard((id: number, name: string) => actions.renameProject(store, id, name)),
    removeProject: guard((id: number) => actions.removeProject(store, id)),
    selectProject: guard((id: number) => actions.selectProject(store, id)),
    rerunVersion: guard((id: number) => actions.rerunVersion(store, id)),
    branchFrom: guard((versionId: number) => actions.branchFrom(store, versionId)),
    cloneCatalogItem: guard((projectId: number, name?: string) =>
      actions.cloneCatalogItem(store, projectId, name),
    ),
    removeCatalogItem: guard((houseId: string, projectId: number) =>
      actions.removeCatalogItem(store, houseId, projectId),
    ),
    reparametrize: guard((versionId: number, params: Record<string, import("./api").ParamValue>) =>
      actions.reparametrize(store, versionId, params),
    ),
    showVersion: (id: number) => actions.showVersion(store, id),
    recallPrompt: (text: string) => store.set({ recalledPrompt: text }),
    clearRecalled: () => store.set({ recalledPrompt: null }),
    generate: (prompt: string) => actions.generate(store, prompt),
    refine: (priorVersionId: number, delta: string) => actions.refine(store, priorVersionId, delta),
    chat: (message: string, forge?: boolean) => actions.chat(store, message, forge),
    stopChat: () => actions.stopChat(store),
    steerChat: guard((message: string) => actions.steerChat(store, message)),
    bootstrap: guard(() => actions.bootstrap(store)),
    openShared: guard((projectId: number, versionId?: number) =>
      actions.openShared(store, projectId, versionId),
    ),
  };
}
