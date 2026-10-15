/** Viewport rendering quality: environment lighting, contact shadows,
 * and live display-mode/opacity application. Theme-agnostic (works light + dark). */
import { ContactShadows, Environment, Lightformer } from "@react-three/drei";
import { type RefObject, useEffect, useState } from "react";
import * as THREE from "three";

import { applyAppearance } from "./appearance";
import { useViewport } from "./viewportStore";

/** Image-based lighting from in-scene lightformers — no network/HDRI fetch. */
export function SceneEnvironment() {
  return (
    <Environment resolution={256}>
      <Lightformer intensity={2.2} form="rect" position={[0, 8, 3]} scale={[12, 6, 1]} />
      <Lightformer intensity={1.1} form="rect" position={[-7, 3, -5]} scale={[6, 6, 1]} />
      <Lightformer intensity={1.1} form="rect" position={[7, 3, -5]} scale={[6, 6, 1]} />
    </Environment>
  );
}

/** Apply display mode + opacity to the model's materials, live. */
export function Appearance({ modelRef }: { modelRef: RefObject<THREE.Group> }) {
  const mode = useViewport((s) => s.displayMode);
  const opacity = useViewport((s) => s.opacity);
  useEffect(() => {
    applyAppearance(modelRef.current, mode, opacity);
  }, [mode, opacity, modelRef]);
  return null;
}

export interface GroundConfig {
  pos: [number, number, number];
  scale: number;
  far: number;
}

/** Shadow-plane placement derived purely from the model's bounding box.
 *
 * No absolute size floor: model GLBs range from ~0.01 scene units (houses)
 * to tens of units, and an oversized plane pollutes the Bounds observe refit
 * that runs on every canvas resize — the camera then frames the plane, not
 * the model, and the near plane lands beyond the whole model (#16).
 *
 * `extent` is anywhere else the model will be drawn — an assembly fully taken
 * apart — and the plane covers it too. Sized to the assembled model alone, it
 * would leave a part that slid out sideways with no shadow, and one that left
 * downward sunk through the floor. */
export function groundConfig(
  model: THREE.Box3 | null,
  extent: THREE.Box3 | null = null,
): GroundConfig | null {
  if (!model || model.isEmpty()) return null;
  const box = extent ? model.clone().union(extent) : model;
  const c = box.getCenter(new THREE.Vector3());
  const s = box.getSize(new THREE.Vector3());
  const footprint = Math.max(s.x, s.z);
  if (footprint <= 0) return null;
  return {
    pos: [c.x, box.min.y - s.y * 0.002, c.z],
    scale: footprint * 1.8,
    far: Math.max(s.y * 2, footprint),
  };
}

/** A soft contact shadow grounded at the bottom of the model, and under
 * `extent` as well when one is given. */
export function Ground({
  modelRef,
  extent = null,
}: {
  modelRef: RefObject<THREE.Group>;
  extent?: THREE.Box3 | null;
}) {
  const [cfg, setCfg] = useState<GroundConfig | null>(null);
  useEffect(() => {
    const g = modelRef.current;
    if (!g || g.children.length === 0) return;
    setCfg(groundConfig(new THREE.Box3().setFromObject(g), extent));
  }, [modelRef, extent]);

  if (!cfg) return null;
  return (
    <ContactShadows
      position={cfg.pos}
      scale={cfg.scale}
      blur={2.5}
      opacity={0.35}
      far={cfg.far}
      resolution={512}
    />
  );
}
