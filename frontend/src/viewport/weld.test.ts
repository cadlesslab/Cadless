/** Weld util (#14): OCCT GLBs tessellate each B-Rep face as its own primitive,
 * so coincident seam vertices are duplicated — sub-pixel cracks shimmer as
 * black lines while the camera orbits. weldModel merges sibling primitives and
 * welds duplicate vertices. */
import * as THREE from "three";
import { describe, expect, it } from "vitest";

import { weldModel } from "./weld";

/** An indexed unit quad in the XY plane at z=0, offset by (x0, y0). */
function quad(x0: number, y0: number): THREE.BufferGeometry {
  const g = new THREE.BufferGeometry();
  // prettier-ignore
  const pos = new Float32Array([
    x0, y0, 0,  x0 + 1, y0, 0,  x0 + 1, y0 + 1, 0,  x0, y0 + 1, 0,
  ]);
  const nrm = new Float32Array([0, 0, 1, 0, 0, 1, 0, 0, 1, 0, 0, 1]);
  g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  g.setAttribute("normal", new THREE.BufferAttribute(nrm, 3));
  g.setIndex([0, 1, 2, 0, 2, 3]);
  return g;
}

function vertexCount(root: THREE.Object3D): number {
  let n = 0;
  root.traverse((o) => {
    if ((o as THREE.Mesh).isMesh) {
      n += (o as THREE.Mesh).geometry.getAttribute("position").count;
    }
  });
  return n;
}

function triangleCount(root: THREE.Object3D): number {
  let n = 0;
  root.traverse((o) => {
    if ((o as THREE.Mesh).isMesh) {
      const g = (o as THREE.Mesh).geometry;
      n += (g.index ? g.index.count : g.getAttribute("position").count) / 3;
    }
  });
  return n;
}

describe("weldModel", () => {
  it("merges sibling primitives and welds shared-edge vertices", () => {
    // Two coplanar quads sharing the x=1 edge — as separate primitives the two
    // seam vertices exist in both meshes (the GLB exporter's output shape).
    const mat = new THREE.MeshStandardMaterial();
    const group = new THREE.Group();
    group.add(new THREE.Mesh(quad(0, 0), mat), new THREE.Mesh(quad(1, 0), mat));

    expect(vertexCount(group)).toBe(8);
    weldModel(group);

    // one mesh, seam verts deduplicated (8 -> 6), triangles preserved
    const meshes: THREE.Mesh[] = [];
    group.traverse((o) => {
      if ((o as THREE.Mesh).isMesh) meshes.push(o as THREE.Mesh);
    });
    expect(meshes).toHaveLength(1);
    expect(vertexCount(group)).toBe(6);
    expect(triangleCount(group)).toBe(4);
    expect(meshes[0].material).toBe(mat);
  });

  it("does not merge meshes with different materials", () => {
    const group = new THREE.Group();
    group.add(
      new THREE.Mesh(quad(0, 0), new THREE.MeshStandardMaterial()),
      new THREE.Mesh(quad(1, 0), new THREE.MeshStandardMaterial()),
    );
    weldModel(group);
    const meshes: THREE.Mesh[] = [];
    group.traverse((o) => {
      if ((o as THREE.Mesh).isMesh) meshes.push(o as THREE.Mesh);
    });
    expect(meshes).toHaveLength(2);
  });

  it("does not weld across crease edges (different normals stay split)", () => {
    // Same positions on the seam but perpendicular normals — welding them
    // would corrupt shading, so they must remain distinct vertices.
    const g2 = quad(1, 0);
    const nrm = g2.getAttribute("normal") as THREE.BufferAttribute;
    for (let i = 0; i < nrm.count; i++) nrm.setXYZ(i, 1, 0, 0);
    const mat = new THREE.MeshStandardMaterial();
    const group = new THREE.Group();
    group.add(new THREE.Mesh(quad(0, 0), mat), new THREE.Mesh(g2, mat));

    weldModel(group);
    expect(vertexCount(group)).toBe(8);
  });

  it("is idempotent (safe on drei's cached scenes)", () => {
    const mat = new THREE.MeshStandardMaterial();
    const group = new THREE.Group();
    group.add(new THREE.Mesh(quad(0, 0), mat), new THREE.Mesh(quad(1, 0), mat));
    weldModel(group);
    const after = vertexCount(group);
    weldModel(group);
    expect(vertexCount(group)).toBe(after);
  });
});
