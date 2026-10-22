/** Viewport rendering quality: environment lighting, contact shadows,
 * and live display-mode/opacity application. Theme-agnostic (works light + dark). */
import { ContactShadows, Environment, Lightformer } from "@react-three/drei";
import { type RefObject, useEffect } from "react";
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

export interface GridConfig {
  cellSize: number;
  sectionSize: number;
  fadeDistance: number;
  /** The length of each axis line. */
  axes: number;
}

/** Where the camera starts, before any model has been framed. */
export const START_CAMERA: [number, number, number] = [80, 80, 80];

/** The longest side of the model a view that has framed none is sized for: one
 * the starting camera frames, so the grid it shows is in view. */
const UNFRAMED_MODEL = START_CAMERA[0];

/** The grid and axes at the size of the model in view.
 *
 * A cell is the power of ten nearest below a quarter of the model's longest
 * side, a section five cells, the fade sixty and an axis two: the proportions
 * the grid was drawn in when a scene unit was taken for a millimetre, kept for
 * a scene in metres in which models run from millimetres to a house. */
export function gridConfig(model: THREE.Box3 | null): GridConfig {
  const size = model && !model.isEmpty() ? model.getSize(new THREE.Vector3()) : null;
  const longest = size ? Math.max(size.x, size.y, size.z) : UNFRAMED_MODEL;
  const exponent = Math.floor(Math.log10((longest > 0 ? longest : UNFRAMED_MODEL) / 4));
  // Divided rather than multiplied below a unit, so a cell is the decimal it reads as.
  const cell = (multiple: number) =>
    exponent < 0 ? multiple / 10 ** -exponent : multiple * 10 ** exponent;
  return { cellSize: cell(1), sectionSize: cell(5), fadeDistance: cell(60), axes: cell(2) };
}

/** Hands up the box of the model once it is drawn, for what is sized from it
 * outside the model's own subtree. Measures once, when it mounts. */
export function ModelBox({
  modelRef,
  onBox,
}: {
  modelRef: RefObject<THREE.Group>;
  onBox: (box: THREE.Box3) => void;
}) {
  useEffect(() => {
    const g = modelRef.current;
    if (!g || g.children.length === 0) return;
    onBox(new THREE.Box3().setFromObject(g));
  }, [modelRef, onBox]);
  return null;
}

/** Shadow-plane placement derived purely from the model's bounding box.
 *
 * No absolute size floor: model GLBs range from thousandths of a scene unit (a
 * small part, in metres) to tens of units (a house), and an oversized plane
 * pollutes the Bounds observe refit that runs on every canvas resize — the
 * camera then frames the plane, not the model, and the near plane lands beyond
 * the whole model (#16).
 *
 * `extent` is anywhere else the model will be drawn — an assembly fully taken
 * apart — and the plane spreads under it too, so a part that slid out sideways
 * keeps its shadow. Its height stays at the assembled model's base: lowered to
 * a part that left downward, the plane would hang below the model at rest,
 * which is the view a reader sees most, while a part below the plane is only
 * there while the assembly is apart. */
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
  const height = model.getSize(new THREE.Vector3()).y;
  return {
    pos: [c.x, model.min.y - height * 0.002, c.z],
    scale: footprint * 1.8,
    far: Math.max(s.y * 2, footprint),
  };
}

/** A soft contact shadow grounded at the bottom of the model, and under
 * `extent` as well when one is given. */
export function Ground({
  model,
  extent = null,
}: {
  model: THREE.Box3 | null;
  extent?: THREE.Box3 | null;
}) {
  const cfg = groundConfig(model, extent);
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
