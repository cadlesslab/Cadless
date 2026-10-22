/** Ground-config derivation (#16): the contact-shadow plane must be sized
 * from the model's box only. A fixed 1-unit floor made the plane ~200x larger
 * than a ~0.01-unit model — a part about 10 mm across, the scene being in
 * metres; any Bounds observe refit (e.g. collapsing the chat panel resizes the
 * canvas) then framed the plane instead of the model and pushed the near plane
 * past the whole of it. */
import * as THREE from "three";
import { describe, expect, it } from "vitest";

import { gridConfig, groundConfig } from "./effects";

function boxAround(size: [number, number, number]): THREE.Box3 {
  const half = new THREE.Vector3(...size).multiplyScalar(0.5);
  return new THREE.Box3(half.clone().negate(), half);
}

describe("groundConfig", () => {
  it("scales with the model, with no absolute floor", () => {
    // a part about 10 mm across: ~0.009 x 0.0087 x 0.010 (x/y/z) scene units
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

  describe("with an assembly taken apart", () => {
    // The model spans -1..1 on every axis; one part leaves 4 units along +x.
    const model = boxAround([2, 2, 2]);
    const sideways = new THREE.Box3(new THREE.Vector3(-1, -1, -1), new THREE.Vector3(5, 1, 1));

    it("spreads under the parts that left, not just the assembled model", () => {
      const cfg = groundConfig(model, sideways)!;
      expect(cfg.pos[0]).toBeCloseTo(2, 6); // centred on -1..5
      expect(cfg.scale).toBeGreaterThanOrEqual(6);
    });

    it("stays under the assembled model when a part leaves downward", () => {
      // Lowered to the part that left, the plane would hang below the model at
      // rest -- the view most readers see -- with the shadow detached from it.
      const below = new THREE.Box3(new THREE.Vector3(-1, -3, -1), new THREE.Vector3(1, 1, 1));
      const y = groundConfig(model, below)!.pos[1];
      expect(y).toBeLessThan(-1);
      expect(y).toBeGreaterThan(-1.1);
    });
  });
});

describe("gridConfig", () => {
  // The scene is in metres: an 80 mm part is 0.08 units, a 12.4 m house 12.4.
  it("gives a part centimetre cells and a house metre cells", () => {
    expect(gridConfig(boxAround([0.08, 0.04, 0.056]))).toEqual({
      cellSize: 0.01, sectionSize: 0.05, fadeDistance: 0.6, axes: 0.02,
    });
    expect(gridConfig(boxAround([12.4, 6.9, 8.4]))).toEqual({
      cellSize: 1, sectionSize: 5, fadeDistance: 60, axes: 2,
    });
  });

  it("follows the model's longest side, whichever side that is", () => {
    expect(gridConfig(boxAround([0.004, 12.4, 0.004])).cellSize).toBe(1);
  });

  it("gives a view that has framed no model the grid its starting camera sees", () => {
    const unframed = { cellSize: 10, sectionSize: 50, fadeDistance: 600, axes: 20 };
    expect(gridConfig(null)).toEqual(unframed);
    expect(gridConfig(new THREE.Box3())).toEqual(unframed);
  });

  it("gives a millimetre part cells that read as the decimals they are", () => {
    // A part 0.4 mm long: its cells are a tenth of a millimetre, exactly.
    expect(gridConfig(boxAround([0.0004, 0.0002, 0.0001])).cellSize).toBe(0.0001);
  });
});
