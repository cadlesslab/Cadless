import { describe, expect, it } from "vitest";

import type { PartRef, Version } from "../api";
import { partRows } from "./partRows";

function version(parts: number, refs?: PartRef[]): Version {
  return {
    id: 5,
    artifacts: Array.from({ length: parts }, (_, part) => ({ kind: "glb", bytes: 1, part })),
    parts: refs,
  } as Version;
}

const ref = (index: number, name: string, color: PartRef["color"] = null): PartRef => ({
  index,
  name,
  path: [],
  color,
});

describe("partRows", () => {
  it("lists every part by the name its build gave it, in part order", () => {
    const rows = partRows(version(3, [ref(0, "chassis"), ref(1, "shell"), ref(2, "wheel_fl")]));
    expect(rows.map((r) => [r.part, r.name])).toEqual([
      [0, "chassis"],
      [1, "shell"],
      [2, "wheel_fl"],
    ]);
  });

  it("numbers a part its build left unnamed, from one", () => {
    const rows = partRows(version(3, [ref(0, "chassis"), ref(1, "part1"), ref(2, "wheel")]));
    expect(rows.map((r) => r.name)).toEqual(["chassis", "part 2", "wheel"]);
  });

  it("numbers every part of a version from a server that sends no names", () => {
    expect(partRows(version(2)).map((r) => r.name)).toEqual(["part 1", "part 2"]);
  });

  it("puts the number beside names that would read alike", () => {
    const rows = partRows(version(3, [ref(0, "wheel"), ref(1, "axle"), ref(2, "wheel")]));
    expect(rows.map((r) => r.name)).toEqual(["wheel (part 1)", "axle", "wheel (part 3)"]);
  });

  it("tells apart a name that reads as another part's number", () => {
    const rows = partRows(version(2, [ref(0, "part 2"), ref(1, "part1")]));
    expect(rows.map((r) => r.name)).toEqual(["part 2 (part 1)", "part 2"]);
  });

  it("puts a name written over several lines on one", () => {
    const rows = partRows(version(2, [ref(0, "front\n wheel"), ref(1, "lid")]));
    expect(rows[0].name).toBe("front wheel");
  });

  it("gives the swatch the colour the viewport draws, and none where there is none", () => {
    const rows = partRows(version(3, [ref(0, "a", [1, 0, 0, 1]), ref(1, "b", [0.5, 0.5, 0.5, 1]), ref(2, "c")]));
    expect(rows.map((r) => r.color)).toEqual([
      "rgba(255, 0, 0, 1)",
      // Linear 0.5 is drawn as sRGB 188, not 128.
      "rgba(188, 188, 188, 1)",
      null,
    ]);
  });

  it("gives no swatch for a colour that is not four numbers, and keeps alpha in range", () => {
    const odd = [1, 0, 0, "1) url(//elsewhere/x.png"] as unknown as PartRef["color"];
    const rows = partRows(version(3, [ref(0, "a", odd), ref(1, "b", [1, 0, 0, 7]), ref(2, "c", [1, 0, Infinity, 1])]));
    expect(rows.map((r) => r.color)).toEqual([null, "rgba(255, 0, 0, 1)", null]);
  });

  it("lists nothing for a model printed whole", () => {
    expect(partRows(version(1, [ref(0, "body")]))).toEqual([]);
  });
});
