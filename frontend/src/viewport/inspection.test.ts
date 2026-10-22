import * as THREE from "three";
import { describe, expect, it } from "vitest";

import {
  countTriangles,
  distanceLabel,
  markerRadius,
  measureAngle,
  measureDistance,
  sectionPlane,
} from "./inspection";

describe("inspection math", () => {
  it("measureDistance is Euclidean", () => {
    expect(measureDistance([0, 0, 0], [3, 4, 0])).toBeCloseTo(5);
    expect(measureDistance([1, 1, 1], [1, 1, 1])).toBe(0);
  });

  it("labels the distance between two points in the scene in millimetres", () => {
    // 12 mm apart, as a model file places them: in metres.
    expect(distanceLabel([0, 0, 0], [0.012, 0, 0])).toBe("12.0 mm");
    expect(distanceLabel([0.1, 0.2, 0], [0.103, 0.204, 0])).toBe("5.0 mm");
  });

  it("sizes a marker by the model it sits on, from a part to a house", () => {
    const box = (x: number, y: number, z: number) =>
      new THREE.Box3(new THREE.Vector3(0, 0, 0), new THREE.Vector3(x, y, z));
    // A 60 mm part and a 12.4 m house, both drawn in metres.
    expect(markerRadius(box(0.06, 0.04, 0.04))).toBeCloseTo(0.0006, 8);
    expect(markerRadius(box(12.4, 6.9, 8.4))).toBeCloseTo(0.124, 8);
    // A tall model: its largest side is the scene's height.
    expect(markerRadius(box(0.02, 0.15, 0.03))).toBeCloseTo(0.0015, 8);
    expect(markerRadius(null)).toBe(0);
  });

  it("measureAngle returns the interior angle in degrees", () => {
    expect(measureAngle([1, 0, 0], [0, 0, 0], [0, 1, 0])).toBeCloseTo(90);
    expect(measureAngle([1, 0, 0], [0, 0, 0], [-1, 0, 0])).toBeCloseTo(180);
    expect(measureAngle([1, 0, 0], [0, 0, 0], [1, 0, 0])).toBeCloseTo(0);
  });

  it("sectionPlane cuts at center for offset 0 and shifts with offset", () => {
    const box = new THREE.Box3(new THREE.Vector3(-10, -5, -2), new THREE.Vector3(10, 5, 2));
    const mid = sectionPlane("x", 0, box);
    expect(mid.normal).toEqual([1, 0, 0]);
    expect(mid.constant).toBeCloseTo(0); // cut at x=0 -> constant=-0

    const shifted = sectionPlane("x", 1, box);
    expect(shifted.constant).toBeCloseTo(-10); // cut at x=+halfExtent(10)

    const z = sectionPlane("z", -1, box);
    expect(z.normal).toEqual([0, 0, 1]);
    expect(z.constant).toBeCloseTo(2); // cut at z=-2 -> constant=+2
  });

  it("countTriangles sums mesh triangles", () => {
    const root = new THREE.Group();
    root.add(new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1))); // box = 12 triangles
    expect(countTriangles(root)).toBe(12);
    expect(countTriangles(null)).toBe(0);
  });
});
