import { describe, expect, it } from "vitest";

import type { Version } from "../api";
import { compareMetrics, versionLabel } from "./versionUtils";

function v(id: number, volume: number, bbox: [number, number, number]): Version {
  return {
    id, project_id: 1, prompt: `prompt ${id}`, code: null, ok: true, error: null,
    volume, bbox, created_at: "", parameters: {}, parent_version_id: null, plan_step: null, artifacts: [],
  };
}

describe("compareMetrics", () => {
  it("produces volume + per-axis rows with deltas (b relative to a)", () => {
    const rows = compareMetrics(v(1, 100, [10, 20, 30]), v(2, 150, [12, 20, 25]));
    expect(rows.map((r) => r.label)).toEqual(["Volume (mm³)", "X (mm)", "Y (mm)", "Z (mm)"]);
    expect(rows[0]).toMatchObject({ a: 100, b: 150, delta: 50 });
    expect(rows[1].delta).toBe(2); // X 10 -> 12
    expect(rows[2].delta).toBe(0); // Y unchanged
    expect(rows[3].delta).toBe(-5); // Z 30 -> 25
  });

  it("handles missing metrics as null deltas", () => {
    const a = { ...v(1, 100, [1, 1, 1]), volume: null } as Version;
    const rows = compareMetrics(a, v(2, 100, [1, 1, 1]));
    expect(rows[0].delta).toBeNull();
  });
});

describe("versionLabel", () => {
  it("prefers the note, falling back to the prompt", () => {
    const ver = v(1, 1, [1, 1, 1]);
    expect(versionLabel(ver)).toBe("prompt 1");
    expect(versionLabel(ver, "  ")).toBe("prompt 1");
    expect(versionLabel(ver, "Bracket")).toBe("Bracket");
  });
});
