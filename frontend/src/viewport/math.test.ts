import * as THREE from "three";
import { describe, expect, it } from "vitest";

import { disposeObject, frameDistance, viewPosition } from "./math";

describe("viewport math", () => {
  it("frameDistance grows with object size and margin", () => {
    const a = frameDistance(10, 50);
    const b = frameDistance(20, 50);
    expect(b).toBeGreaterThan(a);
    expect(frameDistance(10, 50, 2)).toBeGreaterThan(frameDistance(10, 50, 1));
  });

  it("disposeObject disposes geometry and materials", () => {
    const geom = new THREE.BoxGeometry(1, 1, 1);
    const mat = new THREE.MeshStandardMaterial();
    const g = vi.spyOn(geom, "dispose");
    const m = vi.spyOn(mat, "dispose");
    const mesh = new THREE.Mesh(geom, mat);
    disposeObject(mesh);
    expect(g).toHaveBeenCalled();
    expect(m).toHaveBeenCalled();
  });

  it("viewPosition places the camera along the named direction at distance", () => {
    const [x, y, z] = viewPosition("top", [0, 0, 0], 100);
    expect(y).toBeCloseTo(100);
    expect(x).toBeCloseTo(0);
    expect(z).toBeCloseTo(0);

    const iso = viewPosition("iso", [0, 0, 0], 100);
    // equal components, magnitude == distance
    expect(Math.hypot(...iso)).toBeCloseTo(100);
    expect(iso[0]).toBeCloseTo(iso[1]);
    expect(iso[1]).toBeCloseTo(iso[2]);
  });
});
