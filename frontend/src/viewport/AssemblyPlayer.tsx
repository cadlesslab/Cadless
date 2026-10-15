/** Plays an assembly apart and back together in the scene.
 *
 * Must render inside <Bounds>: that is what remounts it with every model, so
 * what it measured always belongs to the parts on screen, and what gives it the
 * framing to widen once the parts start to leave. The position lives in the
 * store rather than here, so whatever else reads or sets it sees the same one;
 * this only advances it while playing and puts each part where it says. The
 * arithmetic is in `assemblyMotion`, where it can be tested without a Canvas. */
import { useBounds } from "@react-three/drei";
import { useFrame } from "@react-three/fiber";
import { type MutableRefObject, type RefObject, useEffect, useRef } from "react";
import * as THREE from "three";

import {
  advancePlay,
  apartBox,
  partOffsets,
  takeApartOrder,
  travelFor,
  type Motion,
} from "./assemblyMotion";
import { viewportStore } from "./viewportStore";

const REDUCED_MOTION = "(prefers-reduced-motion: reduce)";

export function AssemblyPlayer({
  motion,
  modelRef,
  partGroups,
  onApartBox,
}: {
  motion: Motion;
  /** The group every part is drawn under, measured assembled for the travel. */
  modelRef: RefObject<THREE.Group>;
  /** Each part's own group by part ordinal — the thing that is moved. */
  partGroups: MutableRefObject<Map<number, THREE.Group>>;
  /** Told the extent of the parts fully apart, and null when this goes away. */
  onApartBox: (box: THREE.Box3 | null) => void;
}) {
  const bounds = useBounds();
  const travel = useRef(0);
  const apart = useRef<THREE.Box3 | null>(null);
  const framed = useRef(false);
  const wait = useRef(0);
  const reduced = useRef(false);

  useEffect(() => {
    const groups = partGroups.current;
    const model = modelRef.current;
    // Everything is measured assembled, so every part goes home first.
    rest(groups);
    if (!model) return;
    const assembled = new THREE.Box3().setFromObject(model);
    if (assembled.isEmpty()) return;

    // Where nothing can say, motion is not reduced: the player still runs.
    reduced.current = globalThis.matchMedia?.(REDUCED_MOTION).matches ?? false;
    travel.current = travelFor(assembled, motion.explodeFraction);
    const steps = takeApartOrder(motion).length;
    apart.current = apartBox(groups, partOffsets(motion, steps, travel.current));
    framed.current = false;
    onApartBox(apart.current);

    // The parts follow the position the moment it changes, from the store
    // itself rather than on the next frame: whatever reacts to the same change
    // -- a section chosen while the parts are apart sends them home and is
    // measured right away -- has to find them where the position says.
    const place = () => {
      const offsets = partOffsets(motion, viewportStore.get().assemblyPosition, travel.current);
      groups.forEach((group, part) => {
        const offset = offsets[part];
        if (offset) group.position.set(offset[0], offset[1], offset[2]);
      });
    };
    const unsubscribe = viewportStore.subscribe(place);
    viewportStore.setAssemblySteps(steps);
    return () => {
      unsubscribe();
      viewportStore.setAssemblySteps(0);
      onApartBox(null);
      rest(groups);
    };
  }, [motion, modelRef, partGroups, onApartBox]);

  useFrame((_, delta) => {
    const state = viewportStore.get();
    let position = state.assemblyPosition;
    if (state.assemblyPlay) {
      const next = advancePlay(
        { position, wait: wait.current },
        state.assemblySteps,
        state.assemblyPlay,
        delta,
        reduced.current,
      );
      wait.current = next.wait;
      // Only on a change: under reduced motion most frames hold still, and
      // each set wakes every subscriber to say so.
      if (next.position !== position) viewportStore.setAssemblyPosition(next.position);
      if (next.done) viewportStore.stopAssembly();
      position = next.position;
    } else {
      // A play started later begins with its first jump, not a wait.
      wait.current = 0;
    }

    // Framed once, the first time the parts leave: the assembled framing
    // would cut off a part on its way out. Once, so a reader who has moved
    // the camera since is not pulled back to it on every play.
    if (!framed.current && position > 0 && apart.current) {
      framed.current = true;
      bounds.refresh(apart.current).clip().fit();
    }
  });

  return null;
}

function rest(groups: Map<number, THREE.Group>) {
  groups.forEach((group) => group.position.set(0, 0, 0));
}
