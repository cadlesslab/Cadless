import { describe, expect, it } from "vitest";

import type { Version } from "../api";
import { renderWithProviders } from "../test/utils";
import { Inspector } from "./Inspector";

function house(): Version {
  return {
    id: 7, project_id: 1, prompt: "", code: "", ok: true, error: null,
    volume: 158.058e9, bbox: [12400, 8400, 6900], created_at: "", parameters: {},
    parent_version_id: null, plan_step: null, artifacts: [],
  };
}

describe("Inspector", () => {
  it("writes a house's recorded size and volume in metres", () => {
    const { container } = renderWithProviders(<Inspector />, {
      versions: [house()],
      activeVersionId: 7,
    });
    const metrics = [...container.querySelectorAll(".detail-row .metric")].map((m) => m.textContent);
    expect(metrics).toEqual(["158.1 m³", "12.4 × 8.4 × 6.9 m"]);
  });
});
