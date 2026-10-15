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
 * takes one part out over the stretch from j - 1 to j, so a whole number is
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
}

/** Below this a heading is not a direction. The engine's threshold for the same
 * question, kept equal so a part it left standing is never moved here, and a
 * zero-length vector is never normalised into a direction it does not have. */
const HEADING_EPSILON = 1e-9;

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
  const { order, releases, explode_fraction } = raw as Record<string, unknown>;
  if (!Array.isArray(order) || !order.every((p) => Number.isInteger(p))) return null;
  if (!Array.isArray(releases) || releases.length !== parts.length) return null;
  if (!isFiniteNumber(explode_fraction) || explode_fraction <= 0) return null;

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

  return { order: [...(order as number[])], headings, explodeFraction: explode_fraction };
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
 * stretch that `position` has covered, clamped to [0, 1], times `travel`, along
 * that part's heading. `travel` is in scene units. */
export function partOffsets(motion: Motion, position: number, travel: number): Vec3[] {
  const offsets: Vec3[] = motion.headings.map(() => [0, 0, 0]);
  takeApartOrder(motion).forEach((part, j) => {
    const heading = motion.headings[part];
    if (!heading) return;
    const out = Math.min(Math.max(position - j, 0), 1) * travel;
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

function isFiniteNumber(v: unknown): v is number {
  return typeof v === "number" && Number.isFinite(v);
}
