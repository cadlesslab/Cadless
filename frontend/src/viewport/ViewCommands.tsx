/** Applies named-view / fit / reset commands using drei's Bounds API.
 * Must render inside <Bounds>. A named view orients the camera along the view
 * direction, then Bounds frames the model (works for ortho + perspective). */
import { useBounds } from "@react-three/drei";
import { useThree } from "@react-three/fiber";
import { type RefObject, useEffect } from "react";
import * as THREE from "three";

import { VIEW_DIRECTIONS, type ViewName } from "./math";
import { useViewport } from "./viewportStore";

function boxOf(group: THREE.Object3D | null): THREE.Box3 | null {
  if (!group || group.children.length === 0) return null;
  const box = new THREE.Box3().setFromObject(group);
  return box.isEmpty() ? null : box;
}

export function ViewCommands({ modelRef }: { modelRef: RefObject<THREE.Group> }) {
  const bounds = useBounds();
  const command = useViewport((s) => s.command);
  const camera = useThree((s) => s.camera);
  const controls = useThree((s) => s.controls) as { target?: THREE.Vector3; update?: () => void } | null;

  useEffect(() => {
    if (!command) return;
    const box = boxOf(modelRef.current);
    if (!box) return;

    if (command.kind === "fit") {
      bounds.refresh().clip().fit();
      return;
    }
    // named view (or reset -> iso): orient the camera, then frame.
    const view: ViewName = command.kind === "reset" ? "iso" : command.view ?? "iso";
    const center = box.getCenter(new THREE.Vector3());
    const [dx, dy, dz] = VIEW_DIRECTIONS[view];
    const dir = new THREE.Vector3(dx, dy, dz).normalize();
    camera.position.copy(center).addScaledVector(dir, 100);
    camera.up.set(0, 1, 0);
    camera.lookAt(center);
    if (controls?.target) {
      controls.target.copy(center);
      controls.update?.();
    }
    bounds.refresh().clip().fit();
  }, [command, modelRef, bounds, camera, controls]);

  return null;
}
