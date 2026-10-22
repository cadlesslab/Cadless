import { render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import * as THREE from "three";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Measure } from "./inspectionScene";
import { viewportStore } from "./viewportStore";

// The label is HTML laid over the canvas; drawn here as it is, it can be read.
vi.mock("@react-three/drei", () => ({
  Html: ({ children }: { children: ReactNode }) => <>{children}</>,
  Line: () => null,
}));

describe("Measure", () => {
  // jsdom draws three's elements as unknown tags and says so; what is read here
  // is the label and the size the markers are asked for.
  beforeEach(() => vi.spyOn(console, "error").mockImplementation(() => {}));
  afterEach(() => {
    viewportStore.setTool("none");
    vi.restoreAllMocks();
  });

  function measureAcross(model: THREE.Group, a: [number, number, number], b: [number, number, number]) {
    viewportStore.setTool("measure");
    viewportStore.addMeasurePoint(a);
    viewportStore.addMeasurePoint(b);
    return render(<Measure modelRef={{ current: model }} />);
  }

  it("labels two points 12 mm apart on a model drawn in metres as 12.0 mm", () => {
    measureAcross(new THREE.Group(), [0, 0, 0], [0.012, 0, 0]);
    expect(screen.getByText("12.0 mm")).toBeTruthy();
  });

  it("sizes the markers by the model they sit on", () => {
    const part = new THREE.Group();
    part.add(new THREE.Mesh(new THREE.BoxGeometry(0.06, 0.04, 0.04)));
    const { container } = measureAcross(part, [0, 0, 0], [0.012, 0, 0]);
    const radii = [...container.querySelectorAll("sphereGeometry")].map((s) =>
      Number(s.getAttribute("args")?.split(",")[0]),
    );
    // A hundredth of the part's 60 mm, in metres.
    expect(radii).toHaveLength(2);
    for (const radius of radii) expect(radius).toBeCloseTo(0.0006, 8);
  });
});
