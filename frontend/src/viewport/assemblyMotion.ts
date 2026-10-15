/** Where each part of an assembly sits at any point of taking it apart.
 *
 * The engine measured the order the parts go together in and, per part, the
 * direction it was released along when a collision-free way out was found. This
 * turns that into offsets the viewport can apply, as pure functions so the
 * motion can be checked without a Canvas: what a part does at a given moment is
 * the thing a reader is watching, and the one thing a test can hold still.
 *
 * Position runs from 0 (assembled) to K (every moving part out), where K is the
 * number of joints — one per part that has a direction to leave along. Joint j
 * takes one part out over the interval from j - 1 to j, so a whole number is
 * always a part fully home or fully out and never two in motion at once. */
import * as THREE from "three";

export type Vec3 = [number, number, number];

/** A motion that has been checked against the parts on screen.
 *
 * Distinct from the wire payload on purpose: headings are already in the
 * scene's frame and unit length, so nothing that moves a part can forget either
 * step, and a value of this type is one that described what is drawn. */
export interface Motion {
  /** Part ordinals, in the order the parts go together. */
  order: number[];
  /** Indexed by part ordinal: the unit scene direction that part leaves along,
   * or null for a part that stays where it is. */
  headings: (Vec3 | null)[];
  /** How far a part travels, as a fraction of the assembled model's largest
   * dimension. The engine's own fraction, so the viewport and the written
   * guide's exploded frames agree on how far apart "apart" is. */
  explodeFraction: number;
  /** Indexed by part ordinal: how many travels the part goes out once all are
   * apart. The engine's count, the spacing of an exploded view with every part
   * moving, taken as given rather than worked out again from the headings. */
  stretch: number[];
}

/** Below this a heading is not a direction: a zero-length vector is never
 * normalised into a direction it does not have, so a part left standing is
 * never moved. Held equal to the engine's threshold by a mirror test. */
const HEADING_EPSILON = 1e-9;

/** The furthest a part may be sent: the assembled model's own largest size.
 * Further than that and the parts leave the frame rather than show a joint, and
 * a large enough fraction overflows into offsets that are not numbers at all.
 * A mirror test holds the engine's own fraction under it, so the player is not
 * refused for a fraction the engine itself writes. */
const MAX_EXPLODE_FRACTION = 1;

/** A CAD heading (mm, Z-up) as a unit direction in the scene (Y-up).
 *
 * The GLBs are written Z-up to Y-up as (x, y, z) -> (x, z, -y), so a heading
 * has to take the same turn or a part released upward would slide sideways.
 * Null when there is no direction to take: an empty slot is the part left
 * standing, and anything shorter than the threshold is no better. */
export function sceneHeading(h: readonly unknown[]): Vec3 | null {
  if (h.length !== 3 || !h.every(isFiniteNumber)) return null;
  const [x, y, z] = h as number[];
  const length = Math.hypot(x, y, z);
  if (length <= HEADING_EPSILON) return null;
  return [x / length, z / length, -y / length];
}

/** The payload, if it describes the parts on screen; null otherwise.
 *
 * `parts` is the ordinal of each drawn GLB. The order has to name exactly
 * those, once each, and every one of them needs a heading slot — the slots are
 * indexed by ordinal, so a part without one would be moved by its neighbour's.
 * Anything short of that fails closed: the model is drawn as it always was, with
 * no control, rather than played by a list that does not match it. A motion in
 * which nothing moves is refused too, because it would offer a control that
 * does nothing. */
export function parseMotion(raw: unknown, parts: readonly number[]): Motion | null {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) return null;
  const { order, releases, stretch, explode_fraction } = raw as Record<string, unknown>;
  // Counted before anything is walked: an order longer than what is drawn can
  // be refused without reading, copying or sorting it.
  if (!Array.isArray(order) || order.length !== parts.length) return null;
  if (!order.every((p) => Number.isInteger(p))) return null;
  if (!Array.isArray(releases) || releases.length !== parts.length) return null;
  // A part never needs more travels than there are parts for it to clear.
  if (!Array.isArray(stretch) || stretch.length !== parts.length) return null;
  if (!stretch.every((s) => Number.isInteger(s) && s >= 1 && s <= parts.length)) return null;
  if (!isFiniteNumber(explode_fraction)) return null;
  if (explode_fraction <= 0 || explode_fraction > MAX_EXPLODE_FRACTION) return null;

  const drawn = [...parts].sort((a, b) => a - b);
  const named = [...(order as number[])].sort((a, b) => a - b);
  if (named.length !== drawn.length || named.some((p, i) => p !== drawn[i])) return null;
  if (named.some((p) => p < 0 || p >= releases.length)) return null;

  const headings: (Vec3 | null)[] = [];
  for (const slot of releases) {
    if (!Array.isArray(slot)) return null;
    // An empty slot is the part left standing. Anything else has to be a
    // heading, and one that is not three numbers is a payload that is wrong
    // rather than a part that stays.
    if (slot.length !== 0 && (slot.length !== 3 || !slot.every(isFiniteNumber))) return null;
    headings.push(sceneHeading(slot));
  }
  if (!headings.some((h) => h !== null)) return null;

  return {
    order: [...(order as number[])],
    headings,
    explodeFraction: explode_fraction,
    stretch: [...(stretch as number[])],
  };
}

/** The parts in the order they come off: the last one placed first.
 *
 * A part with no heading is skipped rather than given a joint of its own, so
 * every step of the play moves something. */
export function takeApartOrder(motion: Motion): number[] {
  return [...motion.order].reverse().filter((part) => motion.headings[part] != null);
}

/** The next whole joint from `position` in direction `dir`.
 *
 * Part way through a joint, the next one in either direction is that joint's
 * own end, so stepping from a scrubbed position finishes the part in motion
 * rather than skipping past it. */
export function nextJoint(position: number, dir: 1 | -1): number {
  return dir > 0 ? Math.floor(position) + 1 : Math.ceil(position) - 1;
}

/** The offset of every part at `position`, indexed by part ordinal.
 *
 * Joint j (1-based) moves the j-th part to come off by the share of its own
 * stretch that `position` has covered, clamped to [0, 1], along that part's
 * heading. `travel` is in scene units, and a part's stretch is its `stretch`
 * count of travels: more than one where parts after it leave the same way, so
 * the pieces of a beam split end to end do not stop the same distance out and
 * sit joined again. */
export function partOffsets(motion: Motion, position: number, travel: number): Vec3[] {
  const offsets: Vec3[] = motion.headings.map(() => [0, 0, 0]);
  takeApartOrder(motion).forEach((part, j) => {
    const heading = motion.headings[part];
    if (!heading) return;
    const out = Math.min(Math.max(position - j, 0), 1) * travel * motion.stretch[part];
    offsets[part] = [heading[0] * out, heading[1] * out, heading[2] * out];
  });
  return offsets;
}

/** How far a part travels: `fraction` of the box's largest dimension.
 *
 * Measured off the drawn model rather than off the version's recorded bounding
 * box, because the scene is in metres and the record in millimetres — the
 * travel has to be in the units the parts are moved in. */
export function travelFor(box: THREE.Box3, fraction: number): number {
  if (box.isEmpty()) return 0;
  const size = box.getSize(new THREE.Vector3());
  return Math.max(size.x, size.y, size.z) * fraction;
}

/** Seconds one joint takes while playing: long enough to follow which way a
 * part leaves, short enough that an assembly of many parts does not drag. */
export const SECONDS_PER_JOINT = 0.8;

export type PlayDirection = "apart" | "together";

/** Where play has got to: the position, and under reduced motion how long is
 * left before the next jump. */
export interface PlayHead {
  position: number;
  wait: number;
}

/** Play `elapsed` seconds further in direction `play`.
 *
 * Normally the parts slide, one joint per {@link SECONDS_PER_JOINT}. Under
 * reduced motion they jump a whole joint at a time at that same pace instead —
 * the first jump at once, so pressing play visibly does something, and each
 * after a joint's worth of time — because a slide is motion and a jump is a
 * change of state. `done` is whether the end being played toward has been
 * reached, which is the caller's cue to stop. */
export function advancePlay(
  head: PlayHead,
  steps: number,
  play: PlayDirection,
  elapsed: number,
  reduced: boolean,
): PlayHead & { done: boolean } {
  const dir = play === "apart" ? 1 : -1;
  let { position, wait } = head;
  if (reduced) {
    wait -= elapsed;
    if (wait <= 0) {
      position = nextJoint(position, dir);
      wait = SECONDS_PER_JOINT;
    }
  } else {
    position += (dir * elapsed) / SECONDS_PER_JOINT;
  }
  position = Math.min(Math.max(position, 0), steps);
  return { position, wait, done: position === (dir > 0 ? steps : 0) };
}

/** The extent of the assembly once every part is fully out.
 *
 * Each group is measured where it is and moved by its offset, so the groups
 * have to be at rest when this is asked: measured part way, a part would be
 * counted out by its offset twice. A group with no offset is counted where it
 * stands. */
export function apartBox(
  groups: ReadonlyMap<number, THREE.Object3D>,
  offsets: readonly Vec3[],
): THREE.Box3 {
  const box = new THREE.Box3();
  groups.forEach((group, part) => {
    const at = new THREE.Box3().setFromObject(group);
    const offset = offsets[part];
    if (offset) at.translate(new THREE.Vector3(...offset));
    box.union(at);
  });
  return box;
}

function isFiniteNumber(v: unknown): v is number {
  return typeof v === "number" && Number.isFinite(v);
}
