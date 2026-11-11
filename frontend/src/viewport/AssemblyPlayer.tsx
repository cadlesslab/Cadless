/** Plays an assembly apart and back together in the scene.
 *
 * Must render inside <Bounds>: that is what remounts it with every model, so
 * what it measured always belongs to the parts on screen, and what gives it the
 * framing to widen once the parts start to leave. The position lives in the
 * store rather than here, so whatever else reads or sets it sees the same one;
 * this only advances it while playing and puts each part where it says. The
 * arithmetic is in `assemblyMotion`, where it can be tested without a Canvas. */
import { useBounds } from "@react-three/drei";
import { useFrame, useThree } from "@react-three/fiber";
import { type MutableRefObject, type RefObject, useEffect, useRef } from "react";
import * as THREE from "three";

import {
  advancePlay,
  apartBox,
  largestDimension,
  partOffsets,
  type Motion,
} from "./assemblyMotion";
import { viewportStore } from "./viewportStore";

const REDUCED_MOTION = "(prefers-reduced-motion: reduce)";

/** What the player reads of the default controls: where they look, when the
 * reader takes hold of the camera, and how to aim the camera at what they look
 * at. */
type Controls = THREE.EventDispatcher<{ start: object }> & {
  target: THREE.Vector3;
  update: () => void;
};

/** Where the camera stands and what it looks at. */
interface Pose {
  position: THREE.Vector3;
  target: THREE.Vector3;
}

/** A move of the camera from one pose to another, `t` of the way through. */
interface Move {
  from: Pose;
  to: Pose;
  t: number;
}

/** How long a move takes, and how it eases: drei's Bounds' own, so the player's
 * moves read like the viewport's other framing. */
const MOVE_SECONDS = 1;
const ease = (t: number) => 1 - Math.exp(-5 * t) + 0.007 * t;

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
  const camera = useThree((state) => state.camera);
  const controls = useThree((state) => state.controls) as Controls | null;
  const invalidate = useThree((state) => state.invalidate);
  const size = useRef(0);
  const home = useRef<THREE.Box3 | null>(null);
  const apart = useRef<THREE.Box3 | null>(null);
  const framed = useRef(false);
  const before = useRef<Pose | null>(null);
  const move = useRef<Move | null>(null);
  const taken = useRef(false);
  const commanded = useRef<unknown>(null);
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
    size.current = largestDimension(assembled);
    home.current = assembled;
    const steps = motion.joints.length;
    apart.current = apartBox(groups, partOffsets(motion, steps, size.current));
    framed.current = false;
    onApartBox(apart.current);

    // The parts follow the position the moment it changes, from the store
    // itself rather than on the next frame: whatever reacts to the same change
    // -- a section chosen while the parts are apart sends them home and is
    // measured right away -- has to find them where the position says.
    const place = () => {
      const offsets = partOffsets(motion, viewportStore.get().assemblyPosition, size.current);
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

  // A reader who takes hold of the camera while the parts are out has chosen
  // where to look from, and putting the parts back must not take that away; so
  // has one who asks for a view, which the frame below reads off the store.
  useEffect(() => {
    if (!controls) return;
    const take = () => {
      taken.current = true;
      move.current = null;
    };
    controls.addEventListener("start", take);
    return () => controls.removeEventListener("start", take);
  }, [controls]);

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

    // Widened when the parts leave home, since the assembled framing would cut
    // off a part on its way out, and put back once they are home again: drei
    // leaves the camera wherever its last framing put it. Widened from the angle
    // the reader is looking from, not drei's fit, which on a perspective camera
    // re-aims along the line from the new centre and so turns the view as the
    // centre moves — towards the front, for parts that leave upward. Moved by the
    // player itself rather than by drei's lookAt, whose move tilts the camera's
    // up while it runs and leaves it tilted when the reader cuts it short, so
    // every later orbit rolls the horizon.
    if (!framed.current && position > 0 && apart.current) {
      framed.current = true;
      taken.current = false;
      commanded.current = state.command;
      // Where a move of the player's is still heading, if one is: the camera
      // part way through it is not where the reader chose to look from.
      before.current = move.current ? clone(move.current.to) : pose(camera, controls);
      move.current = start(camera, controls, frame(bounds, camera, controls, before.current, apart.current));
    } else if (framed.current && position === 0) {
      framed.current = false;
      // Read off what the reader did, never off where the camera is: the
      // controls' damping carries a drag on after it is let go of, so a camera
      // nobody has touched since is still not where the player left it.
      const chosen = taken.current || state.command !== commanded.current;
      if (!chosen && before.current && home.current) {
        move.current = start(camera, controls, back(bounds, camera, controls, before.current, home.current));
      }
      before.current = null;
    }

    if (move.current && controls) {
      const m = move.current;
      m.t = reduced.current ? 1 : Math.min(1, m.t + delta / MOVE_SECONDS);
      const k = m.t >= 1 ? 1 : ease(m.t);
      camera.position.lerpVectors(m.from.position, m.to.position, k);
      controls.target.lerpVectors(m.from.target, m.to.target, k);
      // Aims the camera at the target about the world's up, which nothing here
      // tilts.
      controls.update();
      invalidate();
      if (m.t >= 1) move.current = null;
    }
  });

  return null;
}

type Bounds = NonNullable<ReturnType<typeof useBounds>>;

function pose(camera: THREE.Camera, controls: Controls | null): Pose {
  const target = controls?.target ?? camera.getWorldDirection(new THREE.Vector3()).add(camera.position);
  return { position: camera.position.clone(), target: target.clone() };
}

function clone(p: Pose): Pose {
  return { position: p.position.clone(), target: p.target.clone() };
}

function orthographic(camera: THREE.Camera): boolean {
  return (camera as THREE.OrthographicCamera).isOrthographicCamera === true;
}

/** The pose that fits `box` from the angle `from` looks along: aimed at the
 * box's centre, at the distance that fits it. An orthographic camera keeps its
 * pose and takes drei's fit, which changes only the zoom. */
function frame(bounds: Bounds, camera: THREE.Camera, controls: Controls | null, from: Pose, box: THREE.Box3): Pose {
  bounds.refresh(box).clip();
  const direction = from.position.clone().sub(from.target);
  if (orthographic(camera) || !controls || direction.lengthSq() === 0) {
    bounds.fit();
    return pose(camera, controls);
  }
  const { center, distance } = bounds.getSize();
  return { position: center.clone().addScaledVector(direction.normalize(), distance), target: center.clone() };
}

/** The reader's pose again, clipped for the model assembled — and, for an
 * orthographic camera, zoomed for it by drei's fit. */
function back(bounds: Bounds, camera: THREE.Camera, controls: Controls | null, to: Pose, home: THREE.Box3): Pose {
  bounds.refresh(home).clip();
  if (orthographic(camera) || !controls) {
    bounds.fit();
    return pose(camera, controls);
  }
  return clone(to);
}

/** A move from where the camera is to `to`, or none where there is nothing the
 * player moves itself. */
function start(camera: THREE.Camera, controls: Controls | null, to: Pose): Move | null {
  if (orthographic(camera) || !controls) return null;
  return { from: pose(camera, controls), to: clone(to), t: 0 };
}

function rest(groups: Map<number, THREE.Group>) {
  groups.forEach((group) => group.position.set(0, 0, 0));
}
