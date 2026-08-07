/** Per-version annotations / rename, persisted in localStorage.
 * The backend has no version-name field, so notes live client-side for the PoC. */
import { useSyncExternalStore } from "react";

const KEY = "cadless-version-notes";
const listeners = new Set<() => void>();

function load(): Record<string, string> {
  try {
    return JSON.parse(localStorage.getItem(KEY) || "{}");
  } catch {
    return {};
  }
}

let cache = load();

export function getNote(versionId: number): string | undefined {
  return cache[String(versionId)];
}

export function setNote(versionId: number, note: string): void {
  cache = { ...cache };
  const trimmed = note.trim();
  if (trimmed) cache[String(versionId)] = trimmed;
  else delete cache[String(versionId)];
  try {
    localStorage.setItem(KEY, JSON.stringify(cache));
  } catch {
    /* storage may be unavailable */
  }
  listeners.forEach((l) => l());
}

function subscribe(l: () => void): () => void {
  listeners.add(l);
  return () => listeners.delete(l);
}

export function useNote(versionId: number): string | undefined {
  return useSyncExternalStore(subscribe, () => cache[String(versionId)]);
}

/** Test hook: reset the in-memory cache from storage. */
export function _reload(): void {
  cache = load();
  listeners.forEach((l) => l());
}
