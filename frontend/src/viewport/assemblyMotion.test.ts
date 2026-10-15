import * as THREE from "three";
import { describe, expect, it } from "vitest";

import {
  nextJoint,
  parseMotion,
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

describe("travelFor", () => {
  it("is the fraction of the model's largest dimension", () => {
    const box = new THREE.Box3(new THREE.Vector3(0, 0, 0), new THREE.Vector3(2, 4, 1));
    expect(travelFor(box, 0.35)).toBeCloseTo(1.4, 9);
  });

  it("is nothing for a model with no extent", () => {
    expect(travelFor(new THREE.Box3(), 0.35)).toBe(0);
  });
});
