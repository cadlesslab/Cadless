/** Geometry welding for loaded GLBs (#14).
 *
 * The OCCT exporter tessellates every B-Rep face as its own glTF primitive, so
 * a house model arrives as hundreds of sibling meshes whose seam vertices are
 * duplicated rather than shared. Unshared seams open sub-pixel cracks that
 * shimmer as dark lines while the camera orbits. Merging sibling primitives
 * (per material) and welding duplicate vertices closes coplanar seams;
 * crease-edge vertices keep distinct normals and are left split, so shading is
 * unchanged. */
import * as THREE from "three";
import {
  mergeGeometries,
  mergeVertices,
} from "three/examples/jsm/utils/BufferGeometryUtils.js";

const WELDED = "__welded";

function isPlainMesh(o: THREE.Object3D): o is THREE.Mesh {
  return (o as THREE.Mesh).isMesh && !(o as THREE.SkinnedMesh).isSkinnedMesh;
}

/** Attribute signature — mergeGeometries requires identical attribute sets. */
function signature(g: THREE.BufferGeometry): string {
  return Object.keys(g.attributes).sort().join(",") + (g.index ? "|i" : "");
}

/** Merge each same-material, same-signature run of sibling meshes into one. */
function mergeSiblings(parent: THREE.Object3D): void {
  const groups = new Map<string, THREE.Mesh[]>();
  for (const child of parent.children) {
    if (!isPlainMesh(child) || Array.isArray(child.material)) continue;
    const key = `${child.material.uuid}|${signature(child.geometry)}`;
    const list = groups.get(key) ?? [];
    list.push(child);
    groups.set(key, list);
  }
  for (const meshes of groups.values()) {
    if (meshes.length < 2) continue;
    const parts = meshes.map((m) => {
      // bake each mesh's local transform so the merged mesh can sit at the
      // parent's frame untransformed
      const g = m.geometry.clone();
      m.updateMatrix();
      if (!m.matrix.equals(new THREE.Matrix4())) g.applyMatrix4(m.matrix);
      return g;
    });
    const merged = mergeGeometries(parts, false);
    parts.forEach((g) => g.dispose());
    if (!merged) continue; // incompatible attributes — leave these siblings be
    const first = meshes[0];
    const replacement = new THREE.Mesh(merged, first.material);
    replacement.name = first.name;
    for (const m of meshes) {
      parent.remove(m);
      m.geometry.dispose();
    }
    parent.add(replacement);
  }
}

/**
 * Weld a loaded model in place: merge sibling primitives per material, then
 * deduplicate vertices whose full attribute sets coincide. Idempotent — safe
 * to call on drei's cached scenes.
 */
export function weldModel(root: THREE.Object3D, tolerance = 1e-4): void {
  if (root.userData[WELDED]) return;
  root.userData[WELDED] = true;

  const parents = new Set<THREE.Object3D>();
  root.traverse((o) => {
    if (isPlainMesh(o) && o.parent) parents.add(o.parent);
  });
  parents.forEach(mergeSiblings);

  root.traverse((o) => {
    if (!isPlainMesh(o)) return;
    const welded = mergeVertices(o.geometry, tolerance);
    if (welded !== o.geometry) {
      o.geometry.dispose();
      o.geometry = welded;
    }
  });
}
