/** The panel that collects getting something out.
 *
 * What is worth pinning here is not that it renders. It is that the printer
 * settings stay reachable when there is nothing to export — setting an address
 * is something somebody does *before* they have a model, and Settings is no
 * longer there to fall back on.
 */
import { screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as api from "../api";
import type { PrintCapability, SettingsStatus, Version } from "../api";
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
    artifacts: kinds.map((k) => ({
      kind: k as Version["artifacts"][number]["kind"], bytes: 1, part: 0,
    })),
  };
}

const withVersion = (v: Version) => ({ versions: [v], activeVersionId: v.id });

/** A build where an address can be recorded: somebody's own machine. */
const LOCAL = {
  slicer_available: true,
  slicer_path: "/usr/bin/prusa-slicer",
  slicer_hint: "",
  printer_configured: false,
  can_configure: true,
  mode: "auto",
  can_send: false,
  can_download: true,
} satisfies PrintCapability;

/** A hosted one, where the settings routes are refused before the engine sees
 * them and no address can exist. */
const HOSTED = { ...LOCAL, can_configure: false } satisfies PrintCapability;

beforeEach(() => vi.mocked(api.fetchPrintCapability).mockResolvedValue(LOCAL));
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

  it("does not offer an address on a build that cannot keep one", async () => {
    // The hosted deployment refuses every settings route at the balancer, so a
    // box here spent a request on a 401 and put the failure on screen as an
    // error nobody could act on. `can_configure` is the engine saying there is
    // nothing to fix rather than nothing set yet.
    vi.mocked(api.fetchPrintCapability).mockResolvedValue(HOSTED);
    vi.mocked(api.getSettings).mockResolvedValue(STATUS);
    renderWithProviders(<ExportPanel />, withVersion(version(["stl"])));

    // The export half is still there; only the address is gone.
    expect(await screen.findByRole("button", { name: "STL" })).toBeInTheDocument();
    expect(screen.queryByLabelText("3D printer address")).not.toBeInTheDocument();
  });

  it("asks for no settings at all where they are refused", async () => {
    // Not merely hiding the box: the request itself is what produced the 401 in
    // the console, and it fired on mount before anybody touched anything.
    vi.mocked(api.fetchPrintCapability).mockResolvedValue(HOSTED);
    vi.mocked(api.getSettings).mockResolvedValue(STATUS);
    renderWithProviders(<ExportPanel />, withVersion(version(["stl"])));

    await screen.findByRole("button", { name: "STL" });
    expect(vi.mocked(api.getSettings)).not.toHaveBeenCalled();
  });

  it("offers nothing rather than guessing when the answer does not say", async () => {
    // A capability that came back without the field is not permission to ask
    // for settings. The guard is `=== true` rather than a truthiness check for
    // this reason: undefined and false have to land on the same side.
    vi.mocked(api.fetchPrintCapability).mockResolvedValue({
      ...LOCAL,
      can_configure: undefined as unknown as boolean,
    });
    vi.mocked(api.getSettings).mockResolvedValue(STATUS);
    renderWithProviders(<ExportPanel />, withVersion(version(["stl"])));

    await screen.findByRole("button", { name: "STL" });
    await waitFor(() => expect(vi.mocked(api.getSettings)).not.toHaveBeenCalled());
    expect(screen.queryByLabelText("3D printer address")).not.toBeInTheDocument();
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
