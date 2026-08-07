/** Pure viewport math — no WebGL, unit-tested.
 * Shared by the r3f viewport and later view-control issues. */
import * as THREE from "three";

/** Camera distance to frame an object of the given max dimension at `fovDeg`. */
export function frameDistance(maxDim: number, fovDeg: number, margin = 1.4): number {
  const fov = (fovDeg * Math.PI) / 180;
  return (maxDim / 2 / Math.tan(fov / 2)) * margin;
}

/** Recursively dispose geometries + materials of a scene subtree. */
export function disposeObject(obj: THREE.Object3D): void {
  obj.traverse((node) => {
    const mesh = node as THREE.Mesh;
    if (mesh.geometry) mesh.geometry.dispose();
    const mat = mesh.material as THREE.Material | THREE.Material[] | undefined;
    if (Array.isArray(mat)) mat.forEach((m) => m.dispose());
    else mat?.dispose();
  });
}

/** Named camera viewpoints (consumes these); unit vectors from target. */
export type ViewName = "iso" | "front" | "back" | "top" | "bottom" | "left" | "right";

export const VIEW_DIRECTIONS: Record<ViewName, [number, number, number]> = {
  iso: [1, 1, 1],
  front: [0, 0, 1],
  back: [0, 0, -1],
  top: [0, 1, 0],
  bottom: [0, -1, 0],
  right: [1, 0, 0],
  left: [-1, 0, 0],
};

/** Camera position for a named view: target + normalized direction * distance. */
export function viewPosition(
  view: ViewName,
  target: [number, number, number],
  distance: number,
): [number, number, number] {
  const dir = VIEW_DIRECTIONS[view];
  const len = Math.hypot(dir[0], dir[1], dir[2]) || 1;
  return [
    target[0] + (dir[0] / len) * distance,
    target[1] + (dir[1] / len) * distance,
    target[2] + (dir[2] / len) * distance,
  ];
}
