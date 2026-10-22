/** Each part drawn as the part list says: shown or hidden, picked out or not.
 *
 * Applied to the group each part is drawn in, so it works the same whether the
 * parts came from one whole-assembly file or a file each. Picking a part out
 * lights its own materials and later puts them back exactly as they were,
 * rather than swapping in other materials: the display mode and the opacity are
 * set on whatever materials are there, and a swapped-in copy would carry them
 * off with it. Kept apart from the Canvas so it can be tested on plain objects. */
import * as THREE from "three";

import { viewportStore } from "./viewportStore";

const HIGHLIGHT_INTENSITY = 0.45;

/** What a material looked like before it was lit, so it can be put back. */
const unlit = new WeakMap<THREE.Material, { emissive: THREE.Color; intensity: number }>();

function light(root: THREE.Object3D, color: THREE.Color | null): void {
  root.traverse((o) => {
    const mesh = o as THREE.Mesh;
    if (!mesh.isMesh) return;
    for (const m of Array.isArray(mesh.material) ? mesh.material : [mesh.material]) {
      // Only a material that can glow: one without an emissive colour is left
      // as it is rather than replaced.
      const mat = m as THREE.MeshStandardMaterial;
      if (!mat.emissive) continue;
      const before = unlit.get(mat);
      if (color) {
        if (!before) unlit.set(mat, { emissive: mat.emissive.clone(), intensity: mat.emissiveIntensity });
        mat.emissive.copy(color);
        mat.emissiveIntensity = HIGHLIGHT_INTENSITY;
      } else if (before) {
        mat.emissive.copy(before.emissive);
        mat.emissiveIntensity = before.intensity;
        unlit.delete(mat);
      }
    }
  });
}

/** Show or hide part `part`, and light it when it is the one picked out. */
export function showPart(
  group: THREE.Object3D,
  part: number,
  hidden: readonly number[],
  highlighted: number | null,
  color: THREE.Color,
): void {
  group.visible = !hidden.includes(part);
  light(group, part === highlighted ? color : null);
}

/** Put a part back as it was loaded: shown, and not picked out. */
export function restorePart(group: THREE.Object3D): void {
  group.visible = true;
  light(group, null);
}

/** Keep every part in `groups` as the store says, from now until the returned
 * function is called.
 *
 * Applied only when what is hidden or picked out changes: the store also
 * changes on every frame an assembly plays, and walking every mesh of the
 * model on each of those would be work that changes nothing. */
export function followPartView(
  groups: Map<number, THREE.Object3D>,
  color: () => THREE.Color,
): () => void {
  let hidden: readonly number[] | null = null;
  let highlighted: number | null = null;
  const apply = () => {
    const state = viewportStore.get();
    if (state.hiddenParts === hidden && state.highlightedPart === highlighted) return;
    hidden = state.hiddenParts;
    highlighted = state.highlightedPart;
    groups.forEach((group, part) => showPart(group, part, state.hiddenParts, state.highlightedPart, color()));
  };
  apply();
  return viewportStore.subscribe(apply);
}

/** The first hit that lands on something drawn.
 *
 * A ray goes through a hidden part as though it were there — three.js tests
 * what a ray crosses, not what is drawn — so a point measured on the first hit
 * could sit on a part nobody can see. */
export function firstDrawnHit<T extends { object: THREE.Object3D }>(
  hits: readonly T[],
): T | undefined {
  return hits.find((hit) => {
    for (let o: THREE.Object3D | null = hit.object; o; o = o.parent) if (!o.visible) return false;
    return true;
  });
}

/** The first hit on `target` that lands on something drawn.
 *
 * A handler is handed the hits of every object that listens, not only its own —
 * the view cube's among them, drawn over the model in a space of its own — so a
 * point measured on the model is taken from the model's hits alone. */
export function firstDrawnHitOn<T extends { object: THREE.Object3D; eventObject: THREE.Object3D }>(
  hits: readonly T[],
  target: THREE.Object3D,
): T | undefined {
  return firstDrawnHit(hits.filter((hit) => hit.eventObject === target));
}

/** Whether the viewport shows the model as it is: no part hidden, none picked
 * out. A picture taken otherwise — a thumbnail kept for the version — would be a
 * picture of something else. */
export function showsModelAsItIs(state: {
  hiddenParts: readonly number[];
  highlightedPart: number | null;
}): boolean {
  return state.hiddenParts.length === 0 && state.highlightedPart === null;
}
