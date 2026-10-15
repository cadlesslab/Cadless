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

  describe("assembly play", () => {
    /** A store holding a model with `k` joints, `apart` of the way through. */
    function playing(k: number, apart = 0): ViewportStore {
      const s = new ViewportStore();
      s.setAssemblySteps(k);
      s.setAssemblyPosition(apart);
      return s;
    }

    it("offers nothing to play until a model says it has joints", () => {
      expect(new ViewportStore().get()).toMatchObject({
        assemblySteps: 0,
        assemblyPosition: 0,
        assemblyPlay: null,
      });
    });

    it("keeps the position between assembled and every joint apart", () => {
      const s = playing(3);
      s.setAssemblyPosition(5);
      expect(s.get().assemblyPosition).toBe(3);
      s.setAssemblyPosition(-1);
      expect(s.get().assemblyPosition).toBe(0);
    });

    it("steps to the next whole joint, finishing one that is part way", () => {
      const s = playing(3, 1.3);
      s.stepAssembly(1);
      expect(s.get().assemblyPosition).toBe(2);
      s.setAssemblyPosition(1.3);
      s.stepAssembly(-1);
      expect(s.get().assemblyPosition).toBe(1);
    });

    it("steps no further than either end", () => {
      const s = playing(3, 3);
      s.stepAssembly(1);
      expect(s.get().assemblyPosition).toBe(3);
      s.setAssemblyPosition(0);
      s.stepAssembly(-1);
      expect(s.get().assemblyPosition).toBe(0);
    });

    it("plays either way until stopped", () => {
      const s = playing(3);
      s.playAssembly("apart");
      expect(s.get().assemblyPlay).toBe("apart");
      s.playAssembly("together");
      expect(s.get().assemblyPlay).toBe("together");
      s.stopAssembly();
      expect(s.get().assemblyPlay).toBeNull();
    });

    it("stops playing when stepped, so the step is where it stays", () => {
      const s = playing(3);
      s.playAssembly("apart");
      s.stepAssembly(1);
      expect(s.get().assemblyPlay).toBeNull();
    });

    it("starts a new model assembled and still", () => {
      const s = playing(3, 2);
      s.playAssembly("apart");
      s.setAssemblySteps(4);
      expect(s.get()).toMatchObject({ assemblySteps: 4, assemblyPosition: 0, assemblyPlay: null });
    });

    it.each(["measure", "section"] as const)(
      "puts the parts back together when %s is chosen",
      (tool) => {
        // Both work on the model as it is assembled: a section box measured
        // around parts that are apart, or a distance between two of them, is a
        // reading of a shape that does not exist.
        const s = playing(3, 2);
        s.playAssembly("apart");
        s.setTool(tool);
        expect(s.get()).toMatchObject({ tool, assemblyPosition: 0, assemblyPlay: null });
      },
    );

    it("leaves the parts where they are when orbiting is chosen", () => {
      const s = playing(3, 2);
      s.setTool("none");
      expect(s.get().assemblyPosition).toBe(2);
    });

    it.each(["measure", "section"] as const)(
      "leaves %s when the parts start to come apart",
      (tool) => {
        const s = playing(3);
        s.setTool(tool);
        s.addMeasurePoint([0, 0, 0]);
        s.setAssemblyPosition(0.5);
        expect(s.get()).toMatchObject({ tool: "none", measurePoints: [], assemblyPosition: 0.5 });
      },
    );
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
