/** Pure inspection math: section plane, measure, triangle count.
 * No React/Canvas — unit-tested. */
import * as THREE from "three";

import type { Axis } from "./viewportStore";

const AXIS_NORMAL: Record<Axis, [number, number, number]> = {
  x: [1, 0, 0],
  y: [0, 1, 0],
  z: [0, 0, 1],
};

/** Clipping plane for a section cut along `axis` at a normalized offset (-1..1)
 * across the bounding box. Returns the plane normal + constant: three keeps the
 * half-space where normal·p + constant >= 0 (i.e. p_axis >= cut). */
export function sectionPlane(
  axis: Axis,
  offsetNorm: number,
  box: THREE.Box3,
): { normal: [number, number, number]; constant: number } {
  const center = box.getCenter(new THREE.Vector3());
  const size = box.getSize(new THREE.Vector3());
  const half: Record<Axis, number> = { x: size.x / 2, y: size.y / 2, z: size.z / 2 };
  const c: Record<Axis, number> = { x: center.x, y: center.y, z: center.z };
  const cut = c[axis] + offsetNorm * half[axis];
  return { normal: AXIS_NORMAL[axis], constant: -cut };
}

export function measureDistance(
  a: [number, number, number],
  b: [number, number, number],
): number {
  return Math.hypot(b[0] - a[0], b[1] - a[1], b[2] - a[2]);
}

/** Interior angle at vertex `b` (degrees) for the path a-b-c. */
export function measureAngle(
  a: [number, number, number],
  b: [number, number, number],
  c: [number, number, number],
): number {
  const ba = new THREE.Vector3(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
  const bc = new THREE.Vector3(c[0] - b[0], c[1] - b[1], c[2] - b[2]);
  if (ba.length() === 0 || bc.length() === 0) return 0;
  const cos = THREE.MathUtils.clamp(ba.normalize().dot(bc.normalize()), -1, 1);
  return (Math.acos(cos) * 180) / Math.PI;
}

/** Total triangle count of a mesh subtree (indexed or non-indexed). */
export function countTriangles(root: THREE.Object3D | null): number {
  if (!root) return 0;
  let tris = 0;
  root.traverse((node) => {
    const mesh = node as THREE.Mesh;
    if (!mesh.isMesh || !mesh.geometry) return;
    const geom = mesh.geometry as THREE.BufferGeometry;
    if (geom.index) tris += geom.index.count / 3;
    else if (geom.attributes.position) tris += geom.attributes.position.count / 3;
  });
  return Math.round(tris);
}
