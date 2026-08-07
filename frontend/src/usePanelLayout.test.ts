import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { clampWidth, loadLayout, MAX_WIDTH, MIN_WIDTH, usePanelLayout } from "./usePanelLayout";

afterEach(() => localStorage.clear());

describe("clampWidth", () => {
  it("clamps to the allowed range", () => {
    expect(clampWidth(10)).toBe(MIN_WIDTH);
    expect(clampWidth(9999)).toBe(MAX_WIDTH);
    expect(clampWidth(400)).toBe(400);
  });
});

describe("usePanelLayout", () => {
  it("persists clamped widths and collapse state to localStorage", () => {
    const { result } = renderHook(() => usePanelLayout());

    act(() => result.current.setLeftWidth(5000));
    expect(result.current.leftWidth).toBe(MAX_WIDTH);

    act(() => result.current.toggleRight());
    expect(result.current.rightCollapsed).toBe(true);

    const stored = loadLayout();
    expect(stored.leftWidth).toBe(MAX_WIDTH);
    expect(stored.rightCollapsed).toBe(true);
  });

  it("reloads persisted layout on mount", () => {
    localStorage.setItem(
      "cadless-panels",
      JSON.stringify({ leftWidth: 300, rightWidth: 420, leftCollapsed: true, rightCollapsed: false }),
    );
    const { result } = renderHook(() => usePanelLayout());
    expect(result.current.leftWidth).toBe(300);
    expect(result.current.rightWidth).toBe(420);
    expect(result.current.leftCollapsed).toBe(true);
  });
});
