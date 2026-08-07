/** Minimal reactive store (framework-agnostic; React binds via useSyncExternalStore). */
import type { ChatEvent, MessageOut, ProgressEvent, Project, Version } from "./api";
import { getStoredTheme, type Theme } from "./theme/theme";

export interface AppState {
  projects: Project[];
  activeProjectId: number | null;
  versions: Version[];
  activeVersionId: number | null;
  generating: boolean;
  events: ProgressEvent[];
  theme: Theme;
  recalledPrompt: string | null; // a prompt pushed into the composer
  // Block-based conversational backbone.
  messages: MessageOut[]; // persisted transcript from GET /messages
  chatEvents: ChatEvent[]; // live POST /chat SSE turn
  chatPending: string | null; // optimistic user message for the in-flight turn
  abortChat: (() => void) | null; // aborts the in-flight chat turn (Stop)
}

export const initialState: AppState = {
  projects: [],
  activeProjectId: null,
  versions: [],
  activeVersionId: null,
  generating: false,
  events: [],
  theme: getStoredTheme(),
  recalledPrompt: null,
  messages: [],
  chatEvents: [],
  chatPending: null,
  abortChat: null,
};

type Listener = (s: AppState) => void;

export class Store {
  private state: AppState;
  private listeners = new Set<Listener>();

  constructor(init: AppState = initialState) {
    this.state = { ...init };
  }

  get(): AppState {
    return this.state;
  }

  set(patch: Partial<AppState>): void {
    this.state = { ...this.state, ...patch };
    for (const l of this.listeners) l(this.state);
  }

  subscribe(fn: Listener): () => void {
    this.listeners.add(fn);
    fn(this.state);
    return () => this.listeners.delete(fn);
  }

  get activeVersion(): Version | null {
    return this.state.versions.find((v) => v.id === this.state.activeVersionId) ?? null;
  }
}
