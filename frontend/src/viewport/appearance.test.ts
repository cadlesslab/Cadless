import * as THREE from "three";
import { describe, expect, it } from "vitest";

import { applyAppearance } from "./appearance";

function sceneWithMesh() {
  const root = new THREE.Group();
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), new THREE.MeshStandardMaterial());
  root.add(mesh);
  return { root, mat: mesh.material as THREE.MeshStandardMaterial };
}

describe("applyAppearance", () => {
  it("wireframe mode sets material.wireframe", () => {
    const { root, mat } = sceneWithMesh();
    applyAppearance(root, "wireframe", 1);
    expect(mat.wireframe).toBe(true);
  });

  it("shaded mode honours opacity and clears wireframe", () => {
    const { root, mat } = sceneWithMesh();
    applyAppearance(root, "shaded", 0.5);
    expect(mat.wireframe).toBe(false);
    expect(mat.opacity).toBe(0.5);
    expect(mat.transparent).toBe(true);
    expect(mat.depthWrite).toBe(true);
  });

  it("opacity 1 in shaded mode is opaque", () => {
    const { root, mat } = sceneWithMesh();
    applyAppearance(root, "shaded", 1);
    expect(mat.transparent).toBe(false);
    expect(mat.opacity).toBe(1);
  });

  it("xray mode caps opacity and disables depth write", () => {
    const { root, mat } = sceneWithMesh();
    applyAppearance(root, "xray", 1);
    expect(mat.transparent).toBe(true);
    expect(mat.opacity).toBeLessThanOrEqual(0.35);
    expect(mat.depthWrite).toBe(false);
  });

  it("is a no-op on null root", () => {
    expect(() => applyAppearance(null, "shaded", 1)).not.toThrow();
  });
});
