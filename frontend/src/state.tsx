/** React bindings for the reactive Store. */
import {
  createContext,
  useContext,
  useSyncExternalStore,
  type ReactNode,
} from "react";

import { type AppState, Store } from "./store";

const StoreContext = createContext<Store | null>(null);

export function StoreProvider({
  store,
  children,
}: {
  store: Store;
  children: ReactNode;
}) {
  return <StoreContext.Provider value={store}>{children}</StoreContext.Provider>;
}

export function useStore(): Store {
  const store = useContext(StoreContext);
  if (!store) throw new Error("useStore must be used within <StoreProvider>");
  return store;
}

/** Subscribe to a derived slice of state; re-renders only when the slice changes. */
export function useStoreSelector<T>(selector: (s: AppState) => T): T {
  const store = useStore();
  return useSyncExternalStore(
    (cb) => store.subscribe(cb),
    () => selector(store.get()),
  );
}

/** The active Version object (or null), derived from active id + versions. */
export function useActiveVersion() {
  return useStoreSelector((s) =>
    s.versions.find((v) => v.id === s.activeVersionId) ?? null,
  );
}

/** The active Project object (or null), derived from active id + projects. */
export function useActiveProject() {
  return useStoreSelector((s) =>
    s.projects.find((p) => p.id === s.activeProjectId) ?? null,
  );
}
