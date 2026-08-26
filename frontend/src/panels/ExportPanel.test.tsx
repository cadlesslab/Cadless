/** The panel that collects getting something out.
 *
 * What is worth pinning here is not that it renders. It is that the printer
 * settings stay reachable when there is nothing to export — setting an address
 * is something somebody does *before* they have a model, and Settings is no
 * longer there to fall back on.
 */
import { screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as api from "../api";
import type { SettingsStatus, Version } from "../api";
import { renderWithProviders } from "../test/utils";
import { ExportPanel } from "./ExportPanel";

vi.mock("../api", async (orig) => ({
  ...(await orig<typeof import("../api")>()),
  getSettings: vi.fn(),
  fetchPrintCapability: vi.fn(),
}));

const STATUS = {
  providers: ["bedrock"],
  provider: "bedrock",
  provider_source: "default",
  orchestrator_model: "m",
  orchestrator_model_source: "default",
  codegen_model: "m",
  codegen_model_source: "default",
  aws_region: "us-east-1",
  aws_region_source: "default",
  printer_address: null,
  printer_bed_width: null,
  printer_bed_depth: null,
  printer_max_height: null,
  printer_nozzle_diameter: null,
  printer_filament_diameter: null,
  printer_filament_density: null,
  printer_cartridge_grams: null,
  printer_nozzle_temperature: null,
  printer_bed_temperature: null,
  secrets: {},
} satisfies SettingsStatus;

function version(kinds: string[], ok = true): Version {
  return {
    id: 5, project_id: 2, prompt: "p", code: null, ok, error: ok ? null : "boom",
    volume: 1, bbox: [1, 1, 1], created_at: "", parameters: {}, parent_version_id: null,
    plan_step: null,
    artifacts: kinds.map((k) => ({ kind: k as Version["artifacts"][number]["kind"], bytes: 1 })),
  };
}

const withVersion = (v: Version) => ({ versions: [v], activeVersionId: v.id });

afterEach(() => vi.clearAllMocks());

describe("ExportPanel", () => {
  it("offers the printer settings even with nothing to export", async () => {
    vi.mocked(api.getSettings).mockResolvedValue(STATUS);
    renderWithProviders(<ExportPanel />);

    expect(await screen.findByLabelText("3D printer address")).toBeInTheDocument();
    expect(screen.getByText(/Nothing to export yet/)).toBeInTheDocument();
  });

  it("explains the address without spending a paragraph on it", async () => {
    // A mark, not a permanent note: the words are the same, and they are read
    // by whoever wants them rather than by everyone on every visit.
    vi.mocked(api.getSettings).mockResolvedValue(STATUS);
    renderWithProviders(<ExportPanel />);

    await screen.findByLabelText("3D printer address");
    expect(screen.getByRole("button", { name: "About the printer address" })).toBeInTheDocument();
    expect(screen.queryByText(/A public address is refused/)).not.toBeInTheDocument();
  });

  it("offers the formats and the actions once there is a version", async () => {
    vi.mocked(api.getSettings).mockResolvedValue(STATUS);
    renderWithProviders(<ExportPanel />, withVersion(version(["step", "stl"])));

    expect(await screen.findByRole("button", { name: "STL" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Print/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "↗ Share" })).toBeInTheDocument();
  });

  it("still offers the printer settings when the version failed", async () => {
    // A failed version has nothing to export and is no reason to hide the
    // machine's own configuration.
    vi.mocked(api.getSettings).mockResolvedValue(STATUS);
    renderWithProviders(<ExportPanel />, withVersion(version([], false)));

    expect(await screen.findByLabelText("3D printer address")).toBeInTheDocument();
    expect(screen.getByText(/nothing to export/i)).toBeInTheDocument();
  });
});
