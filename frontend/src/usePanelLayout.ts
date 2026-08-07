/** Resizable side-panel layout: widths + collapse state, persisted to
 * localStorage (mirrors theme.ts). Widths are clamped to a sane range so a
 * stored/dragged value can never collapse or overflow the workspace. */
import { useCallback, useState } from "react";

export interface PanelLayout {
  leftWidth: number;
  rightWidth: number;
  leftCollapsed: boolean;
  rightCollapsed: boolean;
}

const KEY = "cadless-panels";

export const MIN_WIDTH = 240;
export const MAX_WIDTH = 560;

export const DEFAULT_LAYOUT: PanelLayout = {
  leftWidth: 320,
  rightWidth: 380,
  leftCollapsed: false,
  rightCollapsed: false,
};

export function clampWidth(w: number): number {
  if (Number.isNaN(w)) return DEFAULT_LAYOUT.leftWidth;
  return Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, Math.round(w)));
}

export function loadLayout(): PanelLayout {
  try {
    const raw = typeof localStorage !== "undefined" ? localStorage.getItem(KEY) : null;
    if (!raw) return { ...DEFAULT_LAYOUT };
    const parsed = JSON.parse(raw) as Partial<PanelLayout>;
    return {
      leftWidth: clampWidth(parsed.leftWidth ?? DEFAULT_LAYOUT.leftWidth),
      rightWidth: clampWidth(parsed.rightWidth ?? DEFAULT_LAYOUT.rightWidth),
      leftCollapsed: !!parsed.leftCollapsed,
      rightCollapsed: !!parsed.rightCollapsed,
    };
  } catch {
    return { ...DEFAULT_LAYOUT };
  }
}

function save(layout: PanelLayout): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(layout));
  } catch {
    /* storage may be unavailable (private mode / tests) */
  }
}

export interface PanelLayoutApi extends PanelLayout {
  setLeftWidth: (w: number) => void;
  setRightWidth: (w: number) => void;
  toggleLeft: () => void;
  toggleRight: () => void;
}

export function usePanelLayout(): PanelLayoutApi {
  const [layout, setLayout] = useState<PanelLayout>(loadLayout);

  const update = useCallback((patch: (prev: PanelLayout) => Partial<PanelLayout>) => {
    setLayout((prev) => {
      const merged = { ...prev, ...patch(prev) };
      const next: PanelLayout = {
        ...merged,
        leftWidth: clampWidth(merged.leftWidth),
        rightWidth: clampWidth(merged.rightWidth),
      };
      save(next);
      return next;
    });
  }, []);

  return {
    ...layout,
    setLeftWidth: (w) => update(() => ({ leftWidth: w })),
    setRightWidth: (w) => update(() => ({ rightWidth: w })),
    toggleLeft: () => update((p) => ({ leftCollapsed: !p.leftCollapsed })),
    toggleRight: () => update((p) => ({ rightCollapsed: !p.rightCollapsed })),
  };
}
