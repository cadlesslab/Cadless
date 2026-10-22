import { describe, expect, it } from "vitest";

import type { Version } from "../api";
import { renderWithProviders } from "../test/utils";
import { Readout } from "./Readout";

function version(volume: number, bbox: [number, number, number]): Version {
  return {
    id: 7, project_id: 1, prompt: "", code: "", ok: true, error: null, volume, bbox,
    created_at: "", parameters: {}, parent_version_id: null, plan_step: null, artifacts: [],
  };
}

function readout(v: Version): string[] {
  const { getByLabelText } = renderWithProviders(<Readout />, {
    versions: [v],
    activeVersionId: v.id,
  });
  return [...getByLabelText("Model metrics").children].map((child) => child.textContent ?? "");
}

describe("Readout", () => {
  it("writes a part's recorded size and volume in millimetres", () => {
    expect(readout(version(71347.3, [80, 56, 39]))).toEqual([
      "mm",
      "80.0 × 56.0 × 39.0",
      "71347.3 mm³",
    ]);
  });

  it("writes a house's in metres", () => {
    expect(readout(version(158.058e9, [12400, 8400, 6900]))).toEqual([
      "m",
      "12.4 × 8.4 × 6.9",
      "158.1 m³",
    ]);
  });
});
