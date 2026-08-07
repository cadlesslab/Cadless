/** Ground-config derivation (#16): the contact-shadow plane must be sized
 * from the model's box only. A fixed 1-unit floor made the plane ~200x larger
 * than the ~0.01-unit house GLBs; any Bounds observe refit (e.g. collapsing
 * the chat panel resizes the canvas) then framed the plane instead of the
 * model and pushed the near plane past the whole house. */
import * as THREE from "three";
import { describe, expect, it } from "vitest";

import { groundConfig } from "./effects";

function boxAround(size: [number, number, number]): THREE.Box3 {
  const half = new THREE.Vector3(...size).multiplyScalar(0.5);
  return new THREE.Box3(half.clone().negate(), half);
}

describe("groundConfig", () => {
  it("scales with the model, with no absolute floor", () => {
    // a house GLB is ~0.009 x 0.0087 x 0.010 (x/y/z) scene units
    const cfg = groundConfig(boxAround([0.009, 0.0087, 0.0104]));
    expect(cfg).not.toBeNull();
    expect(cfg!.scale).toBeCloseTo(Math.max(0.009, 0.0104) * 1.8, 6);
    expect(cfg!.scale).toBeLessThan(0.02); // the old 1-unit floor gave 1.8
  });

  it("sits just below the model and reaches past its height", () => {
    const cfg = groundConfig(boxAround([2, 4, 2]))!;
    expect(cfg.pos[1]).toBeLessThan(-2); // below box.min.y
    expect(cfg.pos[1]).toBeGreaterThan(-2.1);
    expect(cfg.far).toBeGreaterThanOrEqual(4); // covers casters above the plane
  });

  it("returns null for an empty box", () => {
    expect(groundConfig(new THREE.Box3())).toBeNull();
    expect(groundConfig(null)).toBeNull();
  });

  it("returns null for a degenerate (zero-footprint) box", () => {
    expect(groundConfig(boxAround([0, 5, 0]))).toBeNull();
  });
});
