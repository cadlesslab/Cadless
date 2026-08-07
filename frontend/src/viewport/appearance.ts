/** Apply a display mode + opacity to a model subtree's materials.
 * Pure (no React/Canvas) so it's unit-testable. */
import * as THREE from "three";

import type { DisplayMode } from "./viewportStore";

const XRAY_MAX_OPACITY = 0.35;

export function applyAppearance(
  root: THREE.Object3D | null,
  mode: DisplayMode,
  opacity: number,
): void {
  if (!root) return;
  root.traverse((node) => {
    const mesh = node as THREE.Mesh;
    if (!mesh.isMesh) return;
    const mats = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
    for (const mat of mats) {
      const m = mat as THREE.Material & { wireframe?: boolean };
      m.wireframe = mode === "wireframe";
      if (mode === "xray") {
        m.transparent = true;
        m.opacity = Math.min(opacity, XRAY_MAX_OPACITY);
        m.depthWrite = false;
      } else {
        m.transparent = opacity < 1;
        m.opacity = opacity;
        m.depthWrite = true;
      }
      m.needsUpdate = true;
    }
  });
}
