import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Project, Version } from "../api";
import * as api from "../api";
import { renderWithProviders } from "../test/utils";
import { ParametersPanel } from "./ParametersPanel";

vi.mock("../api", async (orig) => ({
  ...(await orig<typeof import("../api")>()),
  cloneProject: vi.fn(async () => ({ id: 2 }) as Project),
  reparametrize: vi.fn(async () => ({ ok: true, error: null, version: {} as Version })),
  listProjects: vi.fn(async () => []),
  listVersions: vi.fn(async () => []),
}));

function version(params: Record<string, number>): Version {
  return {
    id: 7, project_id: 1, prompt: "p", code: "result=...", ok: true, error: null,
    volume: 1, bbox: [1, 1, 1], created_at: "", parameters: params, parent_version_id: null,
    plan_step: null, artifacts: [],
  };
}

function project(is_catalog: boolean): Project {
  return { id: 1, name: "Bracket", created_at: "", updated_at: "", current_version_id: 7, is_catalog };
}

const seed = (is_catalog: boolean) => ({
  activeProjectId: 1,
  projects: [project(is_catalog)],
  versions: [version({ length: 40 })],
  activeVersionId: 7,
});

afterEach(() => vi.clearAllMocks());

describe("ParametersPanel", () => {
  it("edits parameters with sliders for a normal (non-catalog) project", () => {
    renderWithProviders(<ParametersPanel />, seed(false));
    expect(screen.getAllByRole("slider").length).toBeGreaterThan(0);
    expect(screen.queryByText(/read-only/i)).toBeNull();
  });

  it("locks editing for a catalog item and offers a Customize action (#22)", async () => {
    renderWithProviders(<ParametersPanel />, seed(true));
    // Read-only: shows the dimension value but no sliders.
    expect(screen.getByText("length")).toBeInTheDocument();
    expect(screen.queryAllByRole("slider")).toHaveLength(0);
    expect(screen.getByText(/read-only/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /customize/i }));
    await waitFor(() =>
      expect(api.cloneProject).toHaveBeenCalledWith(1, "Bracket (copy)"),
    );
  });
});
