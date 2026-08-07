import { describe, expect, it } from "vitest";

import type { Version } from "../api";
import { availableFormats, downloadFilename, shareUrl } from "./exportFormats";

function version(kinds: string[]): Version {
  return {
    id: 5, project_id: 2, prompt: "p", code: null, ok: true, error: null,
    volume: 1, bbox: [1, 1, 1], created_at: "", parameters: {}, parent_version_id: null,
    plan_step: null,
    artifacts: kinds.map((k) => ({ kind: k as Version["artifacts"][number]["kind"], bytes: 1 })),
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

  it("builds a share URL with the project id in the path and the version in ?v", () => {
    const url = shareUrl("https://app.example", "/apps/cadless/", 2, 5);
    expect(url).toBe("https://app.example/apps/cadless/2?v=5");
  });
});
