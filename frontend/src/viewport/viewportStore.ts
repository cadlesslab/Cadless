/** Viewport UI state + an imperative command bus for the r3f camera.
 *
 * The toolbar/ViewCube mutate this store; an in-Canvas CameraRig subscribes and
 * applies camera moves. One-shot actions (view/fit/reset) are dispatched as a
 * command with a monotonically increasing nonce so the rig reacts exactly once.
 * Display-mode/appearance state and inspection extend this. */
import { useSyncExternalStore } from "react";

import type { ViewName } from "./math";

export type Projection = "perspective" | "orthographic";
export type DisplayMode = "shaded" | "wireframe" | "xray";
export type Tool = "none" | "measure" | "section";
export type Axis = "x" | "y" | "z";
export type Point3 = [number, number, number];

export interface ViewCommand {
  kind: "view" | "fit" | "reset";
  view?: ViewName;
  nonce: number;
}

/** Something in the viewer that is not this machine's work.
 *
 * A catalogue held somewhere else is looked at before it is fetched, so the
 * viewer has to draw a mesh belonging to no project here. `url` is null when
 * there is nothing to draw and `note` says why: a viewer that simply goes blank
 * reads as a broken one, and the reason is what tells the two apart. */
export interface Preview {
  url: string | null;
  title: string;
  note?: string | null;
}

export interface ViewportState {
  projection: Projection;
  displayMode: DisplayMode;
  opacity: number;
  gridVisible: boolean;
  axesVisible: boolean;
  command: ViewCommand | null;
  /** What is being looked at in place of the active version, when anything is.
   * Null is the ordinary case, and means the viewer is following this machine. */
  preview: Preview | null;
  // inspection
  tool: Tool;
  sectionAxis: Axis;
  sectionOffset: number; // -1..1 across the bbox extent
  measurePoints: Point3[];
  triangleCount: number | null;
}

const initial: ViewportState = {
  projection: "perspective",
  displayMode: "shaded",
  opacity: 1,
  gridVisible: true,
  axesVisible: true,
  command: null,
  preview: null,
  tool: "none",
  sectionAxis: "z",
  sectionOffset: 0,
  measurePoints: [],
  triangleCount: null,
};

type Listener = () => void;

export class ViewportStore {
  private state: ViewportState = { ...initial };
  private listeners = new Set<Listener>();
  private nonce = 0;

  get = (): ViewportState => this.state;

  subscribe = (fn: Listener): (() => void) => {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  };

  private set(patch: Partial<ViewportState>) {
    this.state = { ...this.state, ...patch };
    this.listeners.forEach((l) => l());
  }

  setProjection(projection: Projection) {
    this.set({ projection });
  }
  toggleProjection() {
    this.setProjection(this.state.projection === "perspective" ? "orthographic" : "perspective");
  }
  setDisplayMode(displayMode: DisplayMode) {
    this.set({ displayMode });
  }
  setOpacity(opacity: number) {
    this.set({ opacity });
  }
  setGridVisible(gridVisible: boolean) {
    this.set({ gridVisible });
  }
  setAxesVisible(axesVisible: boolean) {
    this.set({ axesVisible });
  }

  setTool(tool: Tool) {
    // leaving measure clears its annotations
    this.set({ tool, measurePoints: tool === "measure" ? this.state.measurePoints : [] });
  }
  setSectionAxis(sectionAxis: Axis) {
    this.set({ sectionAxis });
  }
  setSectionOffset(sectionOffset: number) {
    this.set({ sectionOffset });
  }
  addMeasurePoint(p: Point3) {
    // cap at 3 points (segment + angle); a 4th starts a fresh measurement
    const pts = this.state.measurePoints.length >= 3 ? [] : this.state.measurePoints;
    this.set({ measurePoints: [...pts, p] });
  }
  clearMeasure() {
    this.set({ measurePoints: [] });
  }
  setTriangleCount(triangleCount: number | null) {
    this.set({ triangleCount });
  }

  showPreview(preview: Preview) {
    this.set({ preview });
  }
  /** Stop looking at something that is not here.
   *
   * Guarded rather than unconditional: every return to this machine's own work
   * calls it — opening a project, picking a version, a generation finishing —
   * and most of those times there was no preview to end. Setting anyway would
   * wake every subscriber to say nothing changed. */
  clearPreview() {
    if (this.state.preview) this.set({ preview: null });
  }

  private dispatch(kind: ViewCommand["kind"], view?: ViewName) {
    this.set({ command: { kind, view, nonce: ++this.nonce } });
  }
  view(name: ViewName) {
    this.dispatch("view", name);
  }
  fit() {
    this.dispatch("fit");
  }
  reset() {
    this.dispatch("reset");
  }
}

/** Process-wide viewport store (the Canvas + its toolbar share one instance). */
export const viewportStore = new ViewportStore();

export function useViewport<T>(selector: (s: ViewportState) => T): T {
  return useSyncExternalStore(
    viewportStore.subscribe,
    () => selector(viewportStore.get()),
  );
}

/** What a panel shipped outside this tree is given, in place of the store.
 *
 * `plugin.ts` withholds `viewportStore` and says why: a panel that wants to show
 * a model this machine does not hold yet should reach for a capability the
 * engine offers, not a handle it steers. These three are that capability, and
 * they are the whole of it — start showing something, stop showing it, and know
 * whether something is being shown. Anything more would freeze the viewport's
 * internals as public API, which is far more than the feature needs.
 *
 * Bound here rather than re-exported off the class: a method handed over
 * detached would set state on `undefined`, and it would do it silently until the
 * first render that read the result. */
export const showPreview = (preview: Preview): void => viewportStore.showPreview(preview);
export const clearPreview = (): void => viewportStore.clearPreview();

/** Whether the viewer is showing something that is not this machine's work.
 *
 * A boolean rather than the `Preview` itself. Not because the type is private —
 * `showPreview` takes one, so its fields are public either way — but because
 * reading is the side worth keeping loose: a panel wants to know whether to draw
 * an "exit preview" control, and answering with the record would let it come to
 * depend on which fields are set when, and on what the engine puts there for
 * previews the panel did not start. The comparison belongs here. */
export function usePreviewing(): boolean {
  return useViewport((s) => s.preview?.url != null);
}
