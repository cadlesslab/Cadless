import { describe, expect, it } from "vitest";

import { ViewportStore } from "./viewportStore";

describe("ViewportStore", () => {
  it("toggles projection", () => {
    const s = new ViewportStore();
    expect(s.get().projection).toBe("perspective");
    s.toggleProjection();
    expect(s.get().projection).toBe("orthographic");
    s.setProjection("perspective");
    expect(s.get().projection).toBe("perspective");
  });

  it("dispatches view/fit/reset commands with increasing nonces", () => {
    const s = new ViewportStore();
    expect(s.get().command).toBeNull();

    s.view("top");
    const a = s.get().command!;
    expect(a).toMatchObject({ kind: "view", view: "top" });

    s.fit();
    const b = s.get().command!;
    expect(b.kind).toBe("fit");
    expect(b.nonce).toBeGreaterThan(a.nonce);

    s.reset();
    expect(s.get().command!.nonce).toBeGreaterThan(b.nonce);
  });

  it("notifies subscribers on change", () => {
    const s = new ViewportStore();
    let calls = 0;
    const unsub = s.subscribe(() => calls++);
    s.setProjection("orthographic");
    s.fit();
    expect(calls).toBe(2);
    unsub();
    s.fit();
    expect(calls).toBe(2); // no longer notified
  });

  it("tracks display-mode / opacity / grid state (consumed by)", () => {
    const s = new ViewportStore();
    s.setDisplayMode("wireframe");
    s.setOpacity(0.5);
    s.setGridVisible(false);
    expect(s.get()).toMatchObject({ displayMode: "wireframe", opacity: 0.5, gridVisible: false });
  });

  it("measure points accumulate and cap at 3, then restart", () => {
    const s = new ViewportStore();
    s.setTool("measure");
    s.addMeasurePoint([0, 0, 0]);
    s.addMeasurePoint([1, 0, 0]);
    expect(s.get().measurePoints).toHaveLength(2);
    s.addMeasurePoint([1, 1, 0]);
    expect(s.get().measurePoints).toHaveLength(3);
    s.addMeasurePoint([2, 2, 2]); // 4th restarts
    expect(s.get().measurePoints).toEqual([[2, 2, 2]]);
  });

  it("leaving the measure tool clears its points", () => {
    const s = new ViewportStore();
    s.setTool("measure");
    s.addMeasurePoint([0, 0, 0]);
    s.setTool("section");
    expect(s.get().measurePoints).toEqual([]);
  });

  it("tracks section axis + offset", () => {
    const s = new ViewportStore();
    s.setSectionAxis("y");
    s.setSectionOffset(0.4);
    expect(s.get()).toMatchObject({ sectionAxis: "y", sectionOffset: 0.4 });
  });

  it("holds and releases a preview of something not on this machine", () => {
    const s = new ViewportStore();
    expect(s.get().preview).toBeNull();
    s.showPreview({ url: "/depot/artifacts/c/v/a", title: "Bracket" });
    expect(s.get().preview).toEqual({ url: "/depot/artifacts/c/v/a", title: "Bracket" });
    s.clearPreview();
    expect(s.get().preview).toBeNull();
  });

  it("clearing a preview nobody is showing tells nobody", () => {
    // Every return to this machine's work calls it, and most of those times
    // there was nothing to end.
    const s = new ViewportStore();
    let calls = 0;
    s.subscribe(() => calls++);
    s.clearPreview();
    expect(calls).toBe(0);
    s.showPreview({ url: null, title: "Gearbox", note: "no mesh" });
    s.clearPreview();
    expect(calls).toBe(2);
  });
});
