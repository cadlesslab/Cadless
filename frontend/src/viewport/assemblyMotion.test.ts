import * as THREE from "three";
import { describe, expect, it } from "vitest";

import {
  advancePlay,
  apartBox,
  nextJoint,
  parseMotion,
  SECONDS_PER_JOINT,
  partOffsets,
  sceneHeading,
  largestDimension,
  type Motion,
  type Vec3,
} from "./assemblyMotion";
import motionCasesSource from "./motionCases.json?raw";

/** Each component within float noise. `toEqual` would also tell -0 from 0,
 * which nothing that reads a position cares about. */
function expectVec(actual: readonly number[] | null | undefined, expected: Vec3) {
  expect(actual).toBeTruthy();
  expected.forEach((v, i) => expect(actual![i]).toBeCloseTo(v, 9));
}

/** The parts each joint moves, in the order the joints play. */
function partsByJoint(motion: Motion): number[][] {
  return motion.joints.map((joint) => joint.map((movement) => movement.part));
}

// A base that stays put, a part slid on from above (released along CAD +Z), and
// a part slid on from the side (released along CAD +X), placed in that order.
// Written before travel was measured: every part goes the spacing, half the
// model's size.
const PAYLOAD = {
  order: [0, 1, 2],
  releases: [[], [0, 0, 1], [1, 0, 0]],
  stretch: [1, 1, 1],
  explode_fraction: 0.5,
};
const PARTS = [0, 1, 2];

function shelf() {
  const motion = parseMotion(PAYLOAD, PARTS);
  expect(motion).not.toBeNull();
  return motion!;
}

// A tube that stays, with a cap pressed onto each end at the same time: one
// run, so the caps come off together, each along its own heading.
const CAPS = {
  order: [0, 1, 2],
  releases: [[], [1, 0, 0], [-1, 0, 0]],
  stretch: [1, 1, 1],
  explode_fraction: 0.5,
  steps: [[0], [1, 2]],
};

// A base that stays, and a bracket with its pin, fitted to each other off the
// model and then slid onto it from above (CAD +Z) as one piece. The pin reaches
// deeper into the base, so it has further to go than the bracket. Off the
// model, the pin comes out of the bracket along CAD +Y and the bracket stays.
const BRACKET_FIELDS = {
  order: [0, 1, 2],
  releases: [[], [0, 0, 1], [0, 0, 1]],
  stretch: [1, 1, 1],
  explode_fraction: 0.35,
  travel: [0, 0.5, 0.75],
};
const PINNED = { parts: [1, 2], splits: [[], [0, 1, 0]], split_travel: [0, 0.25] };
const BRACKET = { ...BRACKET_FIELDS, steps: [[0], [1, 2]], subassemblies: [PINNED] };

/** The bracket, with `fields` changed in its sub-assembly. */
const pinnedWith = (fields: Record<string, unknown>) => ({
  ...BRACKET,
  subassemblies: [{ ...PINNED, ...fields }],
});

describe("sceneHeading", () => {
  it("turns a CAD +Z heading into scene +Y", () => {
    expectVec(sceneHeading([0, 0, 1]), [0, 1, 0]);
  });

  it("turns a CAD +Y heading into scene -Z", () => {
    expectVec(sceneHeading([0, 1, 0]), [0, 0, -1]);
  });

  it("is a unit direction whatever length the heading arrived at", () => {
    expectVec(sceneHeading([3, 0, 4]), [0.6, 0.8, 0]);
  });

  it("keeps the direction of a heading whose numbers are too large to square", () => {
    expectVec(sceneHeading([1.5e308, 0, 1.5e308]), [Math.SQRT1_2, Math.SQRT1_2, 0]);
  });

  it("is no direction for the part left standing", () => {
    expect(sceneHeading([])).toBeNull();
    expect(sceneHeading([0, 0, 0])).toBeNull();
    expect(sceneHeading([0, 0, 1e-12])).toBeNull();
  });
});

describe("joints", () => {
  it("takes the last part placed off first, one a joint, and skips the part that stays", () => {
    expect(partsByJoint(shelf())).toEqual([[2], [1]]);
  });

  it("plays a run as its parts coming off and then its sub-assemblies coming apart", () => {
    // Last run first: a cover slid off sideways on its own; a pair of clips
    // lifted off together and then parted; and a base of two pieces, which
    // stays, so it only comes apart.
    const motion = parseMotion(
      {
        order: [0, 1, 2, 3, 4],
        releases: [[], [], [0, 0, 1], [0, 0, 1], [1, 0, 0]],
        stretch: [1, 1, 1, 1, 1],
        explode_fraction: 0.35,
        steps: [[0, 1], [2, 3], [4]],
        subassemblies: [
          { parts: [0, 1], splits: [[], [0, 0, 1]], split_travel: [0, 0.3] },
          { parts: [2, 3], splits: [[1, 0, 0], [-1, 0, 0]], split_travel: [0.2, 0.2] },
        ],
      },
      [0, 1, 2, 3, 4],
    )!;
    expect(partsByJoint(motion)).toEqual([[4], [2, 3], [2, 3], [1]]);
  });
});

describe("partOffsets", () => {
  // The model's largest dimension in the scene: the spacing of a half is 10.
  const SIZE = 20;

  it("leaves every part where it is when assembled", () => {
    partOffsets(shelf(), 0, SIZE).forEach((o) => expectVec(o, [0, 0, 0]));
  });

  it("moves only the first joint's part, halfway, at 0.5", () => {
    const at = partOffsets(shelf(), 0.5, SIZE);
    expectVec(at[2], [5, 0, 0]);
    expectVec(at[1], [0, 0, 0]);
  });

  it("has the first part fully out before the next one starts", () => {
    const at = partOffsets(shelf(), 1, SIZE);
    expectVec(at[2], [10, 0, 0]);
    expectVec(at[1], [0, 0, 0]);
  });

  it("has every part that moves out at the last joint, each along its own heading", () => {
    const at = partOffsets(shelf(), 2, SIZE);
    expectVec(at[2], [10, 0, 0]);
    // released along CAD +Z, so it leaves upward in the scene
    expectVec(at[1], [0, 10, 0]);
  });

  it("never moves the part with no heading", () => {
    for (const p of [0, 0.5, 1, 1.5, 2]) expectVec(partOffsets(shelf(), p, SIZE)[0], [0, 0, 0]);
  });

  describe("a beam split into a chain that comes apart along one heading", () => {
    // Part 2 stays; part 1 and then part 0 were slid on along CAD +X, so they
    // come off along CAD -X, part 0 first. Sent the same distance, the two
    // would sit joined again once both are out, and a chain of any length
    // would look like two pieces.
    // The engine's stretch for that chain: part 0 goes two travels, so it clears
    // part 1 once both are out.
    const chain = () =>
      parseMotion(
        { order: [2, 1, 0], releases: [[-1, 0, 0], [-1, 0, 0], []], stretch: [2, 1, 1], explode_fraction: 0.5 },
        PARTS,
      )!;

    it("leaves a gap between every part once all are out", () => {
      const at = partOffsets(chain(), 2, SIZE);
      expectVec(at[1], [-10, 0, 0]);
      expectVec(at[0], [-20, 0, 0]);
    });

    it("sends the first part off the whole way during its own joint", () => {
      const at = partOffsets(chain(), 1, SIZE);
      expectVec(at[0], [-20, 0, 0]);
      expectVec(at[1], [0, 0, 0]);
    });
  });

  it("spaces the parts by the stretch the engine sent, not by a rule of its own", () => {
    // Different headings, yet a stretch of two: a file without a measured
    // travel is played by the engine's count, not one worked out here.
    const motion = parseMotion({ ...PAYLOAD, stretch: [1, 1, 2] }, PARTS)!;
    expectVec(partOffsets(motion, 2, SIZE)[2], [20, 0, 0]);
  });

  it("moves each part by the travel the engine measured, when it sent one", () => {
    // A pin engaged deeper than the spacing goes out far enough to clear its
    // bore: the measured travel replaces the spacing, part by part.
    const motion = parseMotion({ ...PAYLOAD, travel: [0, 1.25, 0.75] }, PARTS)!;
    const at = partOffsets(motion, 2, SIZE);
    expectVec(at[1], [0, 25, 0]);
    expectVec(at[2], [15, 0, 0]);
    expectVec(at[0], [0, 0, 0]);
  });

  describe("parts that went on at the same time", () => {
    const caps = () => parseMotion(CAPS, PARTS)!;

    it("moves both at once, each halfway along its own heading at 0.5", () => {
      const at = partOffsets(caps(), 0.5, SIZE);
      expectVec(at[1], [5, 0, 0]);
      expectVec(at[2], [-5, 0, 0]);
    });

    it("has both fully out at the end of their joint", () => {
      const at = partOffsets(caps(), 1, SIZE);
      expectVec(at[1], [10, 0, 0]);
      expectVec(at[2], [-10, 0, 0]);
    });
  });

  describe("a sub-assembly", () => {
    const bracket = () => parseMotion(BRACKET, PARTS)!;

    it("comes off whole, its members halfway together at 0.5", () => {
      // released along CAD +Z, so the piece rises along scene +Y
      const at = partOffsets(bracket(), 0.5, SIZE);
      expectVec(at[1], [0, 7.5, 0]);
      expectVec(at[2], [0, 7.5, 0]);
    });

    it("comes off as far as the member with furthest to go", () => {
      // The bracket's own travel would leave it 5 short of the pin it holds.
      expectVec(partOffsets(bracket(), 1, SIZE)[1], [0, 15, 0]);
    });

    it("then comes apart, each member's way off the rest added to where the piece went", () => {
      // The pin's split is CAD +Y, scene -Z: halfway out of the bracket at 1.5.
      expectVec(partOffsets(bracket(), 1.5, SIZE)[2], [0, 15, -2.5]);
    });

    it("leaves a member with no split where the piece went", () => {
      expectVec(partOffsets(bracket(), 2, SIZE)[1], [0, 15, 0]);
    });
  });
});

describe("parseMotion", () => {
  it("keeps a motion that names every drawn part", () => {
    const motion = shelf();
    expect(motion.order).toEqual([0, 1, 2]);
  });

  it("takes each part's travel from the spacing when none was measured", () => {
    expect(parseMotion({ ...PAYLOAD, stretch: [1, 1, 2] }, PARTS)!.travel).toEqual([0.5, 0.5, 1]);
  });

  it("takes each part's measured travel as sent", () => {
    expect(parseMotion({ ...PAYLOAD, travel: [0, 1.25, 0.5] }, PARTS)!.travel).toEqual([0, 1.25, 0.5]);
  });

  it("accepts a travel as far as the part count of diagonals and spacings", () => {
    // The viewport's cap: a diagonal and a spacing for each part, which the
    // engine's furthest stays under.
    const furthest = PARTS.length * 2.732;
    expect(parseMotion({ ...PAYLOAD, travel: [0, furthest, 0.5] }, PARTS)).not.toBeNull();
    expect(parseMotion({ ...PAYLOAD, travel: [0, furthest + 0.01, 0.5] }, PARTS)).toBeNull();
  });

  // Every case is a payload that cannot be played over what is drawn: moving
  // parts by a list that does not describe them would put pieces where the
  // engine never measured them going.
  it.each<[string, unknown, number[]]>([
    ["nothing at all", null, PARTS],
    ["a list rather than an object", [0, 1, 2], PARTS],
    ["no order", { ...PAYLOAD, order: undefined }, PARTS],
    ["an order missing a drawn part", { ...PAYLOAD, order: [0, 1] }, PARTS],
    ["an order naming a part twice", { ...PAYLOAD, order: [0, 1, 1] }, PARTS],
    ["an order naming a part not drawn", { ...PAYLOAD, order: [0, 1, 3] }, PARTS],
    ["an order of something other than whole numbers", { ...PAYLOAD, order: [0, 1, 1.5] }, PARTS],
    // Slots sized to what is drawn, so these reach the check of the order
    // against the drawn parts rather than stopping at a slot count.
    [
      "fewer parts drawn than the order names",
      { ...PAYLOAD, releases: [[], [0, 0, 1]], stretch: [1, 1] },
      [0, 1],
    ],
    ["a model drawn whole", { ...PAYLOAD, releases: [], stretch: [] }, []],
    ["a heading slot missing", { ...PAYLOAD, releases: [[], [0, 0, 1]] }, PARTS],
    [
      "more heading slots than drawn parts",
      { ...PAYLOAD, releases: [[], [0, 0, 1], [1, 0, 0], [0, 1, 0]] },
      PARTS,
    ],
    [
      "a drawn part with no heading slot",
      { order: [1, 2], releases: [[0, 0, 1], [1, 0, 0]], stretch: [1, 1], explode_fraction: 0.35 },
      [1, 2],
    ],
    ["a heading that is not three numbers", { ...PAYLOAD, releases: [[], [0, 1], [1, 0, 0]] }, PARTS],
    ["a heading holding a string", { ...PAYLOAD, releases: [[], [0, 0, "1"], [1, 0, 0]] }, PARTS],
    ["no heading that is a direction", { ...PAYLOAD, releases: [[], [0, 0, 0], []] }, PARTS],
    ["no stretch", { ...PAYLOAD, stretch: undefined }, PARTS],
    ["a stretch slot missing", { ...PAYLOAD, stretch: [1, 1] }, PARTS],
    ["a stretch of zero", { ...PAYLOAD, stretch: [1, 0, 1] }, PARTS],
    ["a stretch that is not a whole number", { ...PAYLOAD, stretch: [1, 1.5, 1] }, PARTS],
    // No part needs more travels than there are parts to clear.
    ["a stretch beyond the part count", { ...PAYLOAD, stretch: [1, 4, 1] }, PARTS],
    ["no fraction", { ...PAYLOAD, explode_fraction: undefined }, PARTS],
    ["a fraction of zero", { ...PAYLOAD, explode_fraction: 0 }, PARTS],
    ["a negative fraction", { ...PAYLOAD, explode_fraction: -0.35 }, PARTS],
    ["a fraction written as a string", { ...PAYLOAD, explode_fraction: "0.35" }, PARTS],
    // JSON.parse reads 1e400 as Infinity, so this one can arrive over the wire.
    ["an infinite fraction", { ...PAYLOAD, explode_fraction: Infinity }, PARTS],
    // Finite, but times any travel it overflows, and the offsets come out NaN.
    ["a fraction above one", { ...PAYLOAD, explode_fraction: 1e308 }, PARTS],
    // A measured travel that cannot be played: each one is either not a
    // distance, or moves a part that has to move by nothing at all.
    ["a travel that is not a list", { ...PAYLOAD, travel: 1 }, PARTS],
    ["a travel slot missing", { ...PAYLOAD, travel: [0, 1] }, PARTS],
    ["a travel written as a string", { ...PAYLOAD, travel: [0, "1", 1] }, PARTS],
    ["a negative travel", { ...PAYLOAD, travel: [0, -1, 1] }, PARTS],
    ["an infinite travel", { ...PAYLOAD, travel: [0, Infinity, 1] }, PARTS],
    ["a travel beyond any the engine writes", { ...PAYLOAD, travel: [0, 1e308, 1] }, PARTS],
    ["no travel for a part that moves", { ...PAYLOAD, travel: [0, 0, 1] }, PARTS],
  ])("refuses %s", (_, raw, parts) => {
    expect(parseMotion(raw, parts)).toBeNull();
  });

  it("takes members heading the same way to within the tolerance as sharing a heading", () => {
    // About 5e-7 short of parallel, inside the 1e-6 the engine allows.
    expect(
      parseMotion({ ...BRACKET, releases: [[], [0, 0, 1], [0.001, 0, 1]] }, PARTS),
    ).not.toBeNull();
  });

  it("ignores keys it does not know, in the file and in a sub-assembly", () => {
    const later = { ...pinnedWith({ name: "pin" }), notes: "x" };
    expect(parseMotion(later, PARTS)).toEqual(parseMotion(BRACKET, PARTS));
  });

  // A grouping is held to the same standard: each case is runs that are not the
  // order the parts go together in, or a sub-assembly that could not come off
  // the model as one piece and then apart as the file says.
  it.each<[string, unknown]>([
    ["steps holding null", { ...CAPS, steps: null }],
    ["steps that are not a list", { ...CAPS, steps: { 0: [0], 1: [1, 2] } }],
    ["a run that is not a list", { ...CAPS, steps: [[0], 1, 2] }],
    ["an empty run", { ...CAPS, steps: [[0], [], [1, 2]] }],
    ["runs that leave a part out", { ...CAPS, steps: [[0], [1]] }],
    ["runs naming a part twice", { ...CAPS, steps: [[0], [1, 2], [2]] }],
    ["runs out of the order's sequence", { ...CAPS, steps: [[0], [2, 1]] }],
    // true is not the part numbered 1, whatever a language makes of it.
    ["a run naming a part as true", { ...CAPS, steps: [[0], [true, 2]] }],
    ["sub-assemblies without steps, even none", { ...BRACKET_FIELDS, subassemblies: [] }],
    ["sub-assemblies holding null", { ...BRACKET, subassemblies: null }],
    ["sub-assemblies that are not a list", { ...BRACKET, subassemblies: PINNED }],
    ["a sub-assembly that is not an object", { ...BRACKET, subassemblies: [null] }],
    ["a sub-assembly naming no parts", pinnedWith({ parts: undefined })],
    [
      "a sub-assembly of one part",
      pinnedWith({ parts: [2], splits: [[0, 1, 0]], split_travel: [0.25] }),
    ],
    ["a sub-assembly naming a part twice", pinnedWith({ parts: [2, 2] })],
    ["a sub-assembly naming a part not drawn", pinnedWith({ parts: [1, 3] })],
    ["a sub-assembly naming only parts not drawn", pinnedWith({ parts: [3, 4] })],
    ["a sub-assembly naming a part as true", pinnedWith({ parts: [true, 2] })],
    ["a sub-assembly across two runs", { ...BRACKET, steps: [[0], [1], [2]] }],
    [
      "a part in two sub-assemblies",
      {
        ...BRACKET,
        releases: [[0, 0, 1], [0, 0, 1], [0, 0, 1]],
        travel: [0.5, 0.5, 0.75],
        steps: [[0, 1, 2]],
        subassemblies: [
          PINNED,
          { parts: [0, 1], splits: [[], [1, 0, 0]], split_travel: [0, 0.25] },
        ],
      },
    ],
    // These two with no split travel either, so only the splits are wrong.
    ["a split slot missing", pinnedWith({ splits: [[]], split_travel: [0, 0] })],
    [
      "a split that is not three numbers",
      pinnedWith({ splits: [[], [0, 1]], split_travel: [0, 0] }),
    ],
    ["no split travel", pinnedWith({ split_travel: undefined })],
    ["a split travel slot missing", pinnedWith({ split_travel: [0] })],
    ["a split travel written as a string", pinnedWith({ split_travel: [0, "0.25"] })],
    ["a negative split travel", pinnedWith({ split_travel: [0, -0.25] })],
    [
      "a split travel beyond any the engine writes",
      pinnedWith({ split_travel: [0, PARTS.length * 2.732 + 0.01] }),
    ],
    ["no split travel for a member that comes apart", pinnedWith({ split_travel: [0, 0] })],
    ["a split travel for a member that stays", pinnedWith({ split_travel: [0.1, 0.25] })],
    [
      "members of which only the first leaves the model",
      { ...BRACKET, releases: [[], [0, 0, 1], []] },
    ],
    [
      "members of which only the last leaves the model",
      { ...BRACKET, releases: [[], [], [0, 0, 1]] },
    ],
    [
      "members leaving the model opposite ways",
      { ...BRACKET, releases: [[], [0, 0, 1], [0, 0, -1]] },
    ],
    // About 2e-6 short of parallel: just outside the tolerance.
    [
      "members leaving the model ways just too far apart to share",
      { ...BRACKET, releases: [[], [0, 0, 1], [0.002, 0, 1]] },
    ],
  ])("refuses %s", (_, raw) => {
    expect(parseMotion(raw, PARTS)).toBeNull();
  });
});

/** A case both checks of a motion are held to: this parser, and the engine's
 * check of a file it writes or takes in. */
interface MotionCase {
  name: string;
  parts: number[];
  payload: unknown;
  valid: boolean;
  why: string;
}

// The engine answers these same cases, and the two have to answer each one
// alike: a motion the engine passes and this refuses is a player that never
// appears, and one it refuses that this plays is an item turned away for
// nothing. Kept as JSON so that each side reads the same text with its own
// reader, which is where the two languages part first: 2.0 is an integer here
// and a float there, `true` is no number here and 1 there, and an integer too
// long for a double is Infinity here and an int there. `?raw` rather than
// `node:fs`, which this package type-checks without.
const MOTION_CASES = JSON.parse(motionCasesSource) as MotionCase[];

describe("the motions the engine judges too", () => {
  it.each<[string, MotionCase]>(MOTION_CASES.map((c) => [c.name, c]))("%s", (_, c) => {
    expect(parseMotion(c.payload, c.parts) !== null, c.why).toBe(c.valid);
  });
});

describe("nextJoint", () => {
  it("goes to the next whole joint in the direction asked", () => {
    expect(nextJoint(0, 1)).toBe(1);
    expect(nextJoint(1, 1)).toBe(2);
    expect(nextJoint(2, -1)).toBe(1);
  });

  it("finishes a joint that is part way rather than skipping past it", () => {
    expect(nextJoint(1.3, 1)).toBe(2);
    expect(nextJoint(1.3, -1)).toBe(1);
  });
});

describe("advancePlay", () => {
  const STILL = { position: 0, wait: 0 };

  it("moves one joint in each joint's worth of time", () => {
    const next = advancePlay(STILL, 2, "apart", SECONDS_PER_JOINT / 2, false);
    expect(next.position).toBeCloseTo(0.5, 9);
    expect(next.done).toBe(false);
  });

  it("goes back together at the same pace", () => {
    const next = advancePlay({ position: 1, wait: 0 }, 2, "together", SECONDS_PER_JOINT / 2, false);
    expect(next.position).toBeCloseTo(0.5, 9);
  });

  it("stops at the end it is heading for", () => {
    expect(advancePlay({ position: 1.9, wait: 0 }, 2, "apart", SECONDS_PER_JOINT, false)).toEqual(
      expect.objectContaining({ position: 2, done: true }),
    );
    expect(
      advancePlay({ position: 0.1, wait: 0 }, 2, "together", SECONDS_PER_JOINT, false),
    ).toEqual(expect.objectContaining({ position: 0, done: true }));
  });

  describe("with reduced motion", () => {
    // A frame's worth of time: far less than a joint takes.
    const FRAME = 1 / 60;

    it("moves a whole joint at once rather than sliding there", () => {
      const next = advancePlay(STILL, 2, "apart", FRAME, true);
      expect(next.position).toBe(1);
      expect(next.done).toBe(false);
    });

    it("holds each joint for a joint's worth of time before the next", () => {
      const first = advancePlay(STILL, 3, "apart", FRAME, true);
      const held = advancePlay(first, 3, "apart", FRAME, true);
      expect(held.position).toBe(1);
      const next = advancePlay(held, 3, "apart", SECONDS_PER_JOINT, true);
      expect(next.position).toBe(2);
    });

    it("comes back together a whole joint at a time, and stops there", () => {
      const next = advancePlay({ position: 1, wait: 0 }, 2, "together", FRAME, true);
      expect(next).toEqual(expect.objectContaining({ position: 0, done: true }));
    });
  });
});

describe("apartBox", () => {
  function partAt(x: number): THREE.Group {
    const group = new THREE.Group();
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1));
    mesh.position.set(x, 0, 0);
    group.add(mesh);
    return group;
  }

  it("is every part where it ends up once the assembly is fully apart", () => {
    const groups = new Map([
      [0, partAt(0)],
      [1, partAt(2)],
    ]);
    const box = apartBox(groups, [
      [0, 0, 0],
      [0, 10, 0],
    ]);
    expectVec(box.min.toArray(), [-0.5, -0.5, -0.5]);
    expectVec(box.max.toArray(), [2.5, 10.5, 0.5]);
  });
});

describe("largestDimension", () => {
  it("is the model's largest dimension", () => {
    const box = new THREE.Box3(new THREE.Vector3(0, 0, 0), new THREE.Vector3(2, 4, 1));
    expect(largestDimension(box)).toBeCloseTo(4, 9);
  });

  it("is nothing for a model with no extent", () => {
    expect(largestDimension(new THREE.Box3())).toBe(0);
  });
});
