/** Where each part of an assembly sits at any point of taking it apart.
 *
 * A motion gives the order the parts go together in and, per part, the
 * direction it leaves along: measured by the engine for a build it checked as
 * an assembly, or written by whoever authored a catalog item. A file can also
 * group that order: its `steps` cut it into runs of parts that go
 * on at the same time, and its sub-assemblies are parts of one run fitted to one
 * another first and then to the model as one piece. This turns all of that into
 * offsets the viewport can apply, as pure functions so the motion can be checked
 * without a Canvas: what a part does at a given moment is the thing a reader is
 * watching, and the one thing a test can hold still.
 *
 * What plays is a list of joints, each a set of parts that move at once. Taken
 * apart, the last run comes off first: one joint takes off every part of it that
 * has a direction to leave along, a sub-assembly among them whole, and the next
 * takes the run's sub-assemblies apart. Position goes from 0 (assembled) to K
 * (everything out), where K is the number of joints, and joint j plays over the
 * interval from j to j + 1, so a whole number is always every joint fully home
 * or fully out, never two in motion at once. A file that groups nothing plays
 * one part a joint. */
import * as THREE from "three";

export type Vec3 = [number, number, number];

/** One part's share of a joint: the unit scene direction it moves along, and
 * how far, as a fraction of the assembled model's largest dimension. */
export interface Movement {
  part: number;
  heading: Vec3;
  travel: number;
}

/** A motion that has been checked against the parts on screen.
 *
 * Distinct from the wire payload on purpose: headings are already in the
 * scene's frame and unit length, so nothing that moves a part can forget either
 * one, and a value of this type is one that described what is drawn. */
export interface Motion {
  /** Part ordinals, in the order the parts go together. */
  order: number[];
  /** Indexed by part ordinal: how far the part goes out, as a fraction of the
   * assembled model's largest dimension. The file's own travel where it carries
   * one -- for a build the engine checked, its measure, so the viewport and the
   * written guide's frames agree on how far apart "apart" is; otherwise the
   * spacing an older file carries, its fraction times its count for that part. A member of a
   * sub-assembly comes off the model by the furthest of its members' instead,
   * because the piece comes off as one. */
  travel: number[];
  /** What plays: per joint, in the order they play taking the assembly apart,
   * the parts that move at once. A part moved by more than one joint ends up
   * where their movements add up to. */
  joints: Movement[][];
}

/** Below this a heading is not a direction: a zero-length vector is never
 * normalised into a direction it does not have, so a part left standing is
 * never moved. Held equal to the engine's threshold by a mirror test. */
const HEADING_EPSILON = 1e-9;

/** The largest spacing accepted, as a fraction of the assembled model's
 * largest size: the `explode_fraction` a file without a measured travel is
 * played by, and the spacing in the cap on a measured one. Wider than the model
 * itself and the parts leave the frame rather than show a joint, and a large
 * enough fraction overflows into offsets that are not numbers at all. A mirror
 * test holds the engine's own fraction under it, so the player is not refused
 * for a fraction the engine itself writes. */
const MAX_EXPLODE_FRACTION = 1;

/** The furthest one part's measured travel may be, per part in the assembly.
 *
 * A part goes out beyond every part placed before it, wherever each of those
 * ended up, plus a spacing. Along any heading the assembled model spans at most
 * its diagonal, at most √3 of its largest dimension, so each part placed before
 * adds at most that and a spacing to how far the next has to go: √3 plus the
 * largest spacing accepted. Rounding that down stays safe because a part never
 * has more parts before it than one fewer than the count, and the engine's
 * spacing is well under the largest accepted; the mirror tests hold the
 * engine's furthest under it. Anything beyond is a payload that is wrong rather
 * than a part that is far. A literal, so those tests can read it. */
const MAX_TRAVEL_PER_PART = 2.732;

/** How far short of one the dot product of two unit headings may fall for the
 * two to count as one direction. The members of a sub-assembly come off the
 * model as one piece, so their own headings have to agree to within it. Held
 * equal to the engine's tolerance by a mirror test, so a sub-assembly the
 * engine writes is never refused here. */
export const SAME_HEADING_TOLERANCE = 1e-6;

/** A CAD heading (mm, Z-up) as a unit direction in the scene (Y-up).
 *
 * The GLBs are written Z-up to Y-up as (x, y, z) -> (x, z, -y), so a heading
 * has to take the same turn or a part released upward would slide sideways.
 * Null when there is no direction to take: an empty slot is the part left
 * standing, and anything shorter than the threshold is no better.
 *
 * Measured after dividing by its largest component, so a heading of numbers
 * too large to square still has a length and a direction rather than an
 * infinite length that would turn it into no direction at all. A square root
 * of the squares summed x, y, z rather than `Math.hypot`, whose last bit each
 * runtime rounds its own way: every step here is one IEEE arithmetic rounds
 * exactly alike everywhere, so a check written in another language from the
 * same steps reaches the same bit, at the threshold as anywhere else. */
export function sceneHeading(h: readonly unknown[]): Vec3 | null {
  if (h.length !== 3 || !h.every(isFiniteNumber)) return null;
  const [x, y, z] = h as number[];
  const scale = Math.max(Math.abs(x), Math.abs(y), Math.abs(z));
  if (scale === 0) return null;
  const [a, b, c] = [x / scale, y / scale, z / scale];
  const norm = Math.sqrt(a * a + b * b + c * c);
  if (scale * norm <= HEADING_EPSILON) return null;
  return [a / norm, c / norm, -b / norm];
}

/** The payload, if it describes the parts on screen; null otherwise.
 *
 * `parts` is the ordinal of each drawn GLB. The order has to name exactly
 * those, once each, and every one of them needs a heading slot — the slots are
 * indexed by ordinal, so a part without one would be moved by its neighbour's.
 * Runs, where a file has them, have to be that order cut into pieces, and a
 * sub-assembly has to be parts of one run that leave the model along one
 * heading or not at all. Anything short of that fails closed: the model is drawn
 * as it always was, with no control, rather than played by a list that does not
 * match it. A motion in which nothing moves is refused too, because it would
 * offer a control that does nothing. */
export function parseMotion(raw: unknown, parts: readonly number[]): Motion | null {
  if (!isRecord(raw)) return null;
  const { order, releases, stretch, explode_fraction, travel, steps, subassemblies } = raw;
  // Counted before anything is walked: an order longer than what is drawn can
  // be refused without reading, copying or sorting it.
  if (!Array.isArray(order) || order.length !== parts.length) return null;
  if (!order.every((p) => Number.isInteger(p))) return null;
  if (!Array.isArray(releases) || releases.length !== parts.length) return null;
  // A part never needs more spacings than there are parts for it to clear.
  if (!Array.isArray(stretch) || stretch.length !== parts.length) return null;
  if (!stretch.every((s) => Number.isInteger(s) && s >= 1 && s <= parts.length)) return null;
  if (!isFiniteNumber(explode_fraction)) return null;
  if (explode_fraction <= 0 || explode_fraction > MAX_EXPLODE_FRACTION) return null;

  const drawn = [...parts].sort((a, b) => a - b);
  const named = [...(order as number[])].sort((a, b) => a - b);
  if (named.some((p, i) => p !== drawn[i])) return null;
  if (named.some((p) => p < 0 || p >= releases.length)) return null;

  const headings: (Vec3 | null)[] = [];
  for (const slot of releases) {
    // An empty slot is the part left standing. Anything else has to be a
    // heading, and one that is not three numbers is a payload that is wrong
    // rather than a part that stays.
    if (!isHeadingSlot(slot)) return null;
    headings.push(sceneHeading(slot));
  }
  if (!headings.some((h) => h !== null)) return null;

  // Measured travel is optional: a file written before it was measured plays by
  // the spacing it does carry. Sent, it has to be a distance for every part, and
  // more than none for each part that moves, or a joint would play as nothing.
  const furthest = parts.length * MAX_TRAVEL_PER_PART;
  const isTravel = (t: unknown): t is number => isFiniteNumber(t) && t >= 0 && t <= furthest;
  let travels: number[];
  if (travel === undefined) {
    travels = (stretch as number[]).map((s) => s * explode_fraction);
  } else {
    if (!Array.isArray(travel) || travel.length !== parts.length) return null;
    if (!travel.every(isTravel)) return null;
    if (headings.some((h, part) => h !== null && travel[part] === 0)) return null;
    travels = [...(travel as number[])];
  }

  // The grouping is optional too, and only adds: without runs every part is a
  // run of its own, which plays exactly as a file that predates them. A key
  // that is there has to hold one, though — null is a payload that is wrong,
  // not one that left it out — and sub-assemblies are only ever parts of a run.
  if (subassemblies !== undefined && steps === undefined) return null;
  const runs = readRuns(steps, order as number[]);
  if (!runs) return null;
  const groups = readSubassemblies(subassemblies, runs, headings, isTravel);
  if (!groups) return null;

  return {
    order: [...(order as number[])],
    travel: travels,
    joints: jointsOf(runs, groups, headings, travels),
  };
}

/** The runs `steps` cuts the order into, or null when it does not.
 *
 * Absent, every part is a run of its own. Present, the runs have to be the
 * order itself in pieces: the same parts, each once, in the same sequence, so
 * a part is placed in its run exactly where the order places it. Compared part
 * by part against the order, which holds only whole numbers, so anything that
 * is not one of them — a fraction, a string, `true` — matches nothing, and the
 * walk stops at the first part that does not match. */
function readRuns(steps: unknown, order: readonly number[]): number[][] | null {
  if (steps === undefined) return order.map((part) => [part]);
  if (!Array.isArray(steps)) return null;
  const runs: number[][] = [];
  let placed = 0;
  for (const run of steps) {
    // An empty run would be a joint with nothing in it.
    if (!Array.isArray(run) || run.length === 0) return null;
    if (!run.every((part, i) => part === order[placed + i])) return null;
    runs.push(run);
    placed += run.length;
  }
  return placed === order.length ? runs : null;
}

/** A sub-assembly that has been checked: the run it goes on in, its members,
 * and per member the unit scene direction it comes off the others along —
 * null for a member that stays with them — and how far. */
interface Subassembly {
  run: number;
  parts: number[];
  splits: (Vec3 | null)[];
  splitTravel: number[];
}

/** The sub-assemblies a payload names, or null when one cannot be played.
 *
 * Each has to be two or more parts of one run, in no other sub-assembly, with
 * a split slot and a distance per member; and since the piece is fitted to the
 * model whole, its members have to leave the model along one heading, or none
 * of them leave it at all. */
function readSubassemblies(
  raw: unknown,
  runs: readonly number[][],
  headings: readonly (Vec3 | null)[],
  isTravel: (t: unknown) => t is number,
): Subassembly[] | null {
  if (raw === undefined) return [];
  if (!Array.isArray(raw)) return null;
  const runOf = new Map<number, number>();
  runs.forEach((run, r) => run.forEach((part) => runOf.set(part, r)));
  const grouped = new Set<number>();
  const checked: Subassembly[] = [];
  for (const entry of raw) {
    if (!isRecord(entry)) return null;
    const { parts, splits, split_travel } = entry;
    if (!Array.isArray(parts) || parts.length < 2) return null;
    // Only the order's ordinals have a run, so a member that is not a drawn
    // part — or not a whole number at all — has none. Checked before the
    // splits are read: members that pass are distinct parts of one run, so
    // no list read after them can be longer than the run.
    const run = runOf.get(parts[0]);
    if (run === undefined) return null;
    for (const part of parts) {
      if (runOf.get(part) !== run || grouped.has(part)) return null;
      grouped.add(part);
    }

    if (!Array.isArray(splits) || splits.length !== parts.length) return null;
    if (!splits.every(isHeadingSlot)) return null;
    const ways = splits.map((slot) => sceneHeading(slot));
    // A member with a way off the others goes some distance along it, and one
    // that stays goes none: a split that plays as nothing, or a distance with
    // no direction to cover it in, is a payload that is wrong.
    if (!Array.isArray(split_travel) || split_travel.length !== parts.length) return null;
    if (!split_travel.every((t, i) => isTravel(t) && (ways[i] ? t > 0 : t === 0))) return null;

    const first = headings[parts[0]];
    for (const part of parts) {
      const heading = headings[part];
      const same =
        first === null
          ? heading === null
          : heading !== null && dot(heading, first) >= 1 - SAME_HEADING_TOLERANCE;
      if (!same) return null;
    }
    checked.push({ run, parts, splits: ways, splitTravel: split_travel });
  }
  return checked;
}

/** The joints a checked motion plays, in the order the assembly comes apart.
 *
 * The last run comes off first. Each run plays as one joint taking off every
 * part of it that has a heading, then one taking its sub-assemblies apart —
 * each member with a split leaving the rest — and a joint that would move
 * nothing is not played at all. */
function jointsOf(
  runs: readonly number[][],
  subassemblies: readonly Subassembly[],
  headings: readonly (Vec3 | null)[],
  travel: readonly number[],
): Movement[][] {
  // Each member's travel is how far it has to go to clear what it was engaged
  // with, and the piece moves as one, so every member goes as far as the one
  // with furthest to go: each by its own would pull the piece apart on its way
  // out, and leave the rest short of what the furthest had to clear.
  const reach = [...travel];
  for (const { parts } of subassemblies) {
    const furthest = parts.reduce((far, part) => Math.max(far, travel[part]), 0);
    for (const part of parts) reach[part] = furthest;
  }

  const joints: Movement[][] = [];
  for (let r = runs.length - 1; r >= 0; r--) {
    const off: Movement[] = [];
    for (const part of runs[r]) {
      const heading = headings[part];
      if (heading) off.push({ part, heading, travel: reach[part] });
    }
    const apart: Movement[] = [];
    for (const group of subassemblies) {
      if (group.run !== r) continue;
      group.parts.forEach((part, i) => {
        const heading = group.splits[i];
        if (heading) apart.push({ part, heading, travel: group.splitTravel[i] });
      });
    }
    if (off.length) joints.push(off);
    if (apart.length) joints.push(apart);
  }
  return joints;
}

/** The next whole joint from `position` in direction `dir`.
 *
 * Part way through a joint, the next one in either direction is that joint's
 * own end, so stepping from a scrubbed position finishes the parts in motion
 * rather than skipping past them. */
export function nextJoint(position: number, dir: 1 | -1): number {
  return dir > 0 ? Math.floor(position) + 1 : Math.ceil(position) - 1;
}

/** The offset of every part at `position`, indexed by part ordinal.
 *
 * Joint j moves each of its parts by the share of the movement's travel that
 * `position` has covered past j, clamped to [0, 1], along the movement's
 * heading. A part's offset is the sum over every joint that moves it: a member
 * of a sub-assembly goes out with the piece and then off it, and ends up where
 * the two add up to. `size` is the assembled model's largest dimension in scene
 * units, which every travel is a fraction of. */
export function partOffsets(motion: Motion, position: number, size: number): Vec3[] {
  const offsets: Vec3[] = motion.travel.map(() => [0, 0, 0]);
  motion.joints.forEach((joint, j) => {
    const share = Math.min(Math.max(position - j, 0), 1) * size;
    for (const { part, heading, travel } of joint) {
      const out = share * travel;
      const at = offsets[part];
      at[0] += heading[0] * out;
      at[1] += heading[1] * out;
      at[2] += heading[2] * out;
    }
  });
  return offsets;
}

/** The box's largest dimension: what each part's travel is a fraction of.
 *
 * Measured off the drawn model rather than off the version's recorded bounding
 * box, because the scene is in metres and the record in millimetres — the
 * travel has to be in the units the parts are moved in. */
export function largestDimension(box: THREE.Box3): number {
  if (box.isEmpty()) return 0;
  const size = box.getSize(new THREE.Vector3());
  return Math.max(size.x, size.y, size.z);
}

/** Seconds one joint takes while playing: long enough to follow which way each
 * part leaves, short enough that an assembly of many joints does not drag. */
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

/** Whether a slot is shaped as the payload writes a heading: empty, or three
 * finite numbers. Three numbers too short to be a direction are still a slot —
 * {@link sceneHeading} is what says there is no direction in it. */
function isHeadingSlot(slot: unknown): slot is number[] {
  return (
    Array.isArray(slot) && (slot.length === 0 || (slot.length === 3 && slot.every(isFiniteNumber)))
  );
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function isFiniteNumber(v: unknown): v is number {
  return typeof v === "number" && Number.isFinite(v);
}

/** Two scene headings' dot product, summed in the build's own axis order —
 * x, then y (the scene's third, negated, which the product cancels), then z.
 * A sum of the same terms taken in another order can round a bit either side
 * of the tolerance, so it is taken in the order the file's own frame gives. */
function dot(a: Vec3, b: Vec3): number {
  return a[0] * b[0] + a[2] * b[2] + a[1] * b[1];
}
