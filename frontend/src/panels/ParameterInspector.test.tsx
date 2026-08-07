import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Version } from "../api";
import * as api from "../api";
import { renderWithProviders } from "../test/utils";
import { ParameterInspector, sliderRange } from "./ParameterInspector";

vi.mock("../api", async (orig) => ({
  ...(await orig<typeof import("../api")>()),
  reparametrize: vi.fn(async () => ({ ok: true, error: null, version: {} as Version })),
  listProjects: vi.fn(async () => []),
  listVersions: vi.fn(async () => []),
}));

function version(params: Record<string, number | string>): Version {
  return {
    id: 7, project_id: 1, prompt: "p", code: "result=...", ok: true, error: null,
    volume: 1, bbox: [1, 1, 1], created_at: "", parameters: params, parent_version_id: null,
    plan_step: null, artifacts: [],
  };
}

const project = { id: 1, name: "P", created_at: "", updated_at: "", current_version_id: 7 };

afterEach(() => vi.clearAllMocks());

describe("sliderRange", () => {
  it("spans 0..~2x with a sensible step", () => {
    expect(sliderRange(10)).toMatchObject({ min: 0, max: 20, step: 0.5 });
    expect(sliderRange(3)).toMatchObject({ min: 0, max: 6, step: 0.1 });
  });
});

describe("ParameterInspector", () => {
  it("renders a slider per numeric parameter and reparametrizes on commit", async () => {
    renderWithProviders(<ParameterInspector />, {
      activeProjectId: 1,
      projects: [project],
      versions: [version({ length: 40, hole_dia: 6 })],
      activeVersionId: 7,
    });
    expect(screen.getByText("length")).toBeInTheDocument();
    expect(screen.getByText("hole_dia")).toBeInTheDocument();

    // Radix slider commits on keyboard interaction
    const sliders = screen.getAllByRole("slider");
    sliders[0].focus();
    fireEvent.keyDown(sliders[0], { key: "ArrowRight" });
    await waitFor(() => expect(api.reparametrize).toHaveBeenCalled());
  });

  it("renders nothing when the version has no numeric parameters", () => {
    const { container } = renderWithProviders(<ParameterInspector />, {
      activeProjectId: 1,
      projects: [project],
      versions: [version({})],
      activeVersionId: 7,
    });
    expect(container.querySelector(".params")).toBeNull();
  });

  it("readOnly mode shows values without sliders and never reparametrizes", () => {
    renderWithProviders(<ParameterInspector readOnly />, {
      activeProjectId: 1,
      projects: [project],
      versions: [version({ length: 40 })],
      activeVersionId: 7,
    });
    expect(screen.getByText("length")).toBeInTheDocument();
    expect(screen.getByText("40")).toBeInTheDocument();
    expect(screen.queryAllByRole("slider")).toHaveLength(0);
    expect(api.reparametrize).not.toHaveBeenCalled();
  });
});
