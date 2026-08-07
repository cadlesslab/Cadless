/** Per-version thumbnail cache.
 * Thumbnails are captured from the main canvas when a version is viewed, so we
 * reuse one WebGL context rather than spinning up a renderer per list item. */
import { useSyncExternalStore } from "react";

const cache = new Map<number, string>();
const listeners = new Set<() => void>();

export function setThumbnail(versionId: number, dataUrl: string): void {
  cache.set(versionId, dataUrl);
  listeners.forEach((l) => l());
}

export function getThumbnail(versionId: number): string | undefined {
  return cache.get(versionId);
}

function subscribe(l: () => void): () => void {
  listeners.add(l);
  return () => listeners.delete(l);
}

export function useThumbnail(versionId: number | undefined): string | undefined {
  return useSyncExternalStore(subscribe, () =>
    versionId == null ? undefined : cache.get(versionId),
  );
}
