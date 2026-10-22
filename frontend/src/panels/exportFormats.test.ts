import { describe, expect, it } from "vitest";

import type { Version } from "../api";
import { availableFormats, downloadFilename, partsOf, shareUrl } from "./exportFormats";

type Ref = Version["artifacts"][number];

function version(kinds: string[]): Version {
  return withArtifacts(kinds.map((k) => ({ kind: k as Ref["kind"], bytes: 1, part: 0 })));
}

function withArtifacts(artifacts: Ref[]): Version {
  return {
    id: 5, project_id: 2, prompt: "p", code: null, ok: true, error: null,
    volume: 1, bbox: [1, 1, 1], created_at: "", parameters: {}, parent_version_id: null,
    plan_step: null,
    artifacts,
  };
}

describe("export formats", () => {
  it("lists present formats in display order", () => {
    expect(availableFormats(version(["glb", "step", "obj", "stl"]))).toEqual([
      "step", "stl", "obj", "glb",
    ]);
    expect(availableFormats(version(["step", "glb"]))).toEqual(["step", "glb"]);
    expect(availableFormats(version([]))).toEqual([]);
  });

  it("builds a download filename", () => {
    expect(downloadFilename(5, "stl")).toBe("model_5.stl");
  });

  it("lists the pieces of a kind in part order", () => {
    // Deliberately out of order. Nothing promises the server returns them
    // sorted, and saving piece 2 under piece 0's name is a mistake nobody can
    // see afterwards -- the files are all there and one of them is wrong.
    const v = withArtifacts([
      { kind: "stl", bytes: 1, part: 2 },
      { kind: "step", bytes: 1, part: 0 },
      { kind: "stl", bytes: 1, part: 0 },
      { kind: "stl", bytes: 1, part: 1 },
    ]);
    expect(partsOf(v, "stl").map((a) => a.part)).toEqual([0, 1, 2]);
    expect(partsOf(v, "step").map((a) => a.part)).toEqual([0]);
    expect(partsOf(v, "obj")).toEqual([]);
  });

  it("names each piece of a model apart, and leaves a whole one alone", () => {
    expect(downloadFilename(5, "stl", 0)).toBe("model_5_p0.stl");
    expect(downloadFilename(5, "stl", 2)).toBe("model_5_p2.stl");
  });

  it("puts a piece's name after its number", () => {
    expect(downloadFilename(5, "stl", 2, "wheel_fl")).toBe("model_5_p2_wheel_fl.stl");
    expect(downloadFilename(5, "stl", 2, "R\u00e4der vorn")).toBe("model_5_p2_R\u00e4der_vorn.stl");
  });

  it("cuts a long name by characters, never through one", () => {
    const kept = downloadFilename(5, "stl", 1, "p".repeat(47) + "😀");
    expect(kept).toBe(`model_5_p1_${"p".repeat(47)}😀.stl`);
    const cut = downloadFilename(5, "stl", 1, "p".repeat(48) + "😀");
    expect(cut).toBe(`model_5_p1_${"p".repeat(48)}.stl`);
    expect(/\p{Cs}/u.test(cut)).toBe(false);
  });

  it("writes a name in its composed form", () => {
    expect(downloadFilename(5, "stl", 1, "caf" + "e\u0301")).toBe("model_5_p1_caf\u00e9.stl");
  });

  it("keeps out of a piece's name what a file system will not take", () => {
    expect(downloadFilename(5, "stl", 1, "../lid: top?")).toBe("model_5_p1_lid_top.stl");
    expect(downloadFilename(5, "stl", 1, "  ")).toBe("model_5_p1.stl");
    expect(downloadFilename(5, "stl", 1, null)).toBe("model_5_p1.stl");
  });

  it("builds a share URL with the project id in the path and the version in ?v", () => {
    const url = shareUrl("https://app.example", "/apps/cadless/", 2, 5);
    expect(url).toBe("https://app.example/apps/cadless/2?v=5");
  });
});
