import { act, renderHook } from "@testing-library/react";
import * as THREE from "three";
import { afterEach, describe, expect, it } from "vitest";

import { usePartGroups } from "./usePartGroups";
import { viewportStore } from "./viewportStore";

function part(): { group: THREE.Group; material: THREE.MeshStandardMaterial } {
  const material = new THREE.MeshStandardMaterial({ emissive: 0x111111 });
  return { group: new THREE.Group().add(new THREE.Mesh(new THREE.BoxGeometry(), material)), material };
}

afterEach(() => viewportStore.resetParts());

describe("usePartGroups", () => {
  it("starts a part that mounts after it was hidden hidden", () => {
    const { result } = renderHook(() => usePartGroups(7, "#0000ff"));
    act(() => viewportStore.togglePart(1));
    const late = part();
    act(() => result.current.onPartGroup(1, late.group));
    expect(late.group.visible).toBe(false);
    expect(result.current.partGroups.current.get(1)).toBe(late.group);
  });

  it("hides and picks out a mounted part as the list changes", () => {
    const { result } = renderHook(() => usePartGroups(7, "#0000ff"));
    const a = part();
    act(() => result.current.onPartGroup(0, a.group));
    act(() => viewportStore.togglePart(0));
    expect(a.group.visible).toBe(false);
    act(() => viewportStore.highlightPart(0));
    expect(a.material.emissive.getHex()).toBe(0x0000ff);
  });

  it("puts a part back as loaded when it goes", () => {
    const { result } = renderHook(() => usePartGroups(7, "#0000ff"));
    const a = part();
    act(() => result.current.onPartGroup(0, a.group));
    act(() => viewportStore.highlightPart(0));
    act(() => result.current.onPartGroup(0, null));
    expect(a.material.emissive.getHex()).toBe(0x111111);
    expect(result.current.partGroups.current.has(0)).toBe(false);
  });

  it("forgets what was hidden when the version changes", () => {
    const { rerender } = renderHook(({ id }) => usePartGroups(id, "#0000ff"), {
      initialProps: { id: 7 },
    });
    act(() => viewportStore.togglePart(2));
    rerender({ id: 8 });
    expect(viewportStore.get().hiddenParts).toEqual([]);
  });
});
