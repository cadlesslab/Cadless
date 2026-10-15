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
  takeApartOrder,
  travelFor,
  type Vec3,
} from "./assemblyMotion";

/** Each component within float noise. `toEqual` would also tell -0 from 0,
 * which nothing that reads a position cares about. */
function expectVec(actual: readonly number[] | null | undefined, expected: Vec3) {
  expect(actual).toBeTruthy();
  expected.forEach((v, i) => expect(actual![i]).toBeCloseTo(v, 9));
}

// A base that stays put, a part slid on from above (released along CAD +Z), and
// a part slid on from the side (released along CAD +X), placed in that order.
const PAYLOAD = { order: [0, 1, 2], releases: [[], [0, 0, 1], [1, 0, 0]], explode_fraction: 0.35 };
const PARTS = [0, 1, 2];

function shelf() {
  const motion = parseMotion(PAYLOAD, PARTS);
  expect(motion).not.toBeNull();
  return motion!;
}

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

  it("is no direction for the part left standing", () => {
    expect(sceneHeading([])).toBeNull();
    expect(sceneHeading([0, 0, 0])).toBeNull();
    expect(sceneHeading([0, 0, 1e-12])).toBeNull();
  });
});

describe("takeApartOrder", () => {
  it("takes the last part placed off first, and skips the part that stays", () => {
    expect(takeApartOrder(shelf())).toEqual([2, 1]);
  });
});

describe("partOffsets", () => {
  const TRAVEL = 10;

  it("leaves every part where it is when assembled", () => {
    partOffsets(shelf(), 0, TRAVEL).forEach((o) => expectVec(o, [0, 0, 0]));
  });

  it("moves only the first joint's part, halfway, at 0.5", () => {
    const at = partOffsets(shelf(), 0.5, TRAVEL);
    expectVec(at[2], [5, 0, 0]);
    expectVec(at[1], [0, 0, 0]);
  });

  it("has the first part fully out before the next one starts", () => {
    const at = partOffsets(shelf(), 1, TRAVEL);
    expectVec(at[2], [10, 0, 0]);
    expectVec(at[1], [0, 0, 0]);
  });

  it("has every part that moves out at the last joint, each along its own heading", () => {
    const at = partOffsets(shelf(), 2, TRAVEL);
    expectVec(at[2], [10, 0, 0]);
    // released along CAD +Z, so it leaves upward in the scene
    expectVec(at[1], [0, 10, 0]);
  });

  it("never moves the part with no heading", () => {
    for (const p of [0, 0.5, 1, 1.5, 2]) expectVec(partOffsets(shelf(), p, TRAVEL)[0], [0, 0, 0]);
  });

  describe("a beam split into a chain that comes apart along one heading", () => {
    // Part 2 stays; part 1 and then part 0 were slid on along CAD +X, so they
    // come off along CAD -X, part 0 first. Sent the same distance, the two
    // would sit joined again once both are out, and a chain of any length
    // would look like two pieces.
    const chain = () =>
      parseMotion({ order: [2, 1, 0], releases: [[-1, 0, 0], [-1, 0, 0], []], explode_fraction: 0.35 }, PARTS)!;

    it("leaves a gap between every part once all are out", () => {
      const at = partOffsets(chain(), 2, TRAVEL);
      expectVec(at[1], [-10, 0, 0]);
      expectVec(at[0], [-20, 0, 0]);
    });

    it("sends the first part off the whole way during its own joint", () => {
      const at = partOffsets(chain(), 1, TRAVEL);
      expectVec(at[0], [-20, 0, 0]);
      expectVec(at[1], [0, 0, 0]);
    });
  });
});

describe("parseMotion", () => {
  it("keeps a motion that names every drawn part", () => {
    const motion = shelf();
    expect(motion.order).toEqual([0, 1, 2]);
    expect(motion.explodeFraction).toBe(0.35);
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
    ["fewer parts drawn than the order names", PAYLOAD, [0, 1]],
    ["a model drawn whole", PAYLOAD, []],
    ["a heading slot missing", { ...PAYLOAD, releases: [[], [0, 0, 1]] }, PARTS],
    [
      "more heading slots than drawn parts",
      { ...PAYLOAD, releases: [[], [0, 0, 1], [1, 0, 0], [0, 1, 0]] },
      PARTS,
    ],
    [
      "a drawn part with no heading slot",
      { order: [1, 2], releases: [[0, 0, 1], [1, 0, 0]], explode_fraction: 0.35 },
      [1, 2],
    ],
    ["a heading that is not three numbers", { ...PAYLOAD, releases: [[], [0, 1], [1, 0, 0]] }, PARTS],
    ["a heading holding a string", { ...PAYLOAD, releases: [[], [0, 0, "1"], [1, 0, 0]] }, PARTS],
    ["no heading that is a direction", { ...PAYLOAD, releases: [[], [0, 0, 0], []] }, PARTS],
    ["no fraction", { ...PAYLOAD, explode_fraction: undefined }, PARTS],
    ["a fraction of zero", { ...PAYLOAD, explode_fraction: 0 }, PARTS],
    ["a negative fraction", { ...PAYLOAD, explode_fraction: -0.35 }, PARTS],
    ["a fraction written as a string", { ...PAYLOAD, explode_fraction: "0.35" }, PARTS],
    // JSON.parse reads 1e400 as Infinity, so this one can arrive over the wire.
    ["an infinite fraction", { ...PAYLOAD, explode_fraction: Infinity }, PARTS],
    // Finite, but times any travel it overflows, and the offsets come out NaN.
    ["a fraction above one", { ...PAYLOAD, explode_fraction: 1e308 }, PARTS],
  ])("refuses %s", (_, raw, parts) => {
    expect(parseMotion(raw, parts)).toBeNull();
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

describe("travelFor", () => {
  it("is the fraction of the model's largest dimension", () => {
    const box = new THREE.Box3(new THREE.Vector3(0, 0, 0), new THREE.Vector3(2, 4, 1));
    expect(travelFor(box, 0.35)).toBeCloseTo(1.4, 9);
  });

  it("is nothing for a model with no extent", () => {
    expect(travelFor(new THREE.Box3(), 0.35)).toBe(0);
  });
});
