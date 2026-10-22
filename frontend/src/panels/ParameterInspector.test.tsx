import { fireEvent, screen, waitFor, within } from "@testing-library/react";
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

  const OVERLAP = "left and right share 500.0 mm^3 of interior volume";

  /** Commit one slider step, after setting what the edit comes back as. */
  async function edit(
    response: api.ReparametrizeResponse,
    current: number,
  ): Promise<ReturnType<typeof renderWithProviders>> {
    vi.mocked(api.reparametrize).mockResolvedValueOnce(response);
    vi.mocked(api.listProjects).mockResolvedValueOnce([{ ...project, current_version_id: current }]);
    vi.mocked(api.listVersions).mockResolvedValueOnce([
      version({ gap: 5 }),
      { ...version({ gap: 6 }), id: 8, ok: response.ok },
    ]);
    const rendered = renderWithProviders(<ParameterInspector />, {
      activeProjectId: 1,
      projects: [project],
      versions: [version({ gap: 5 })],
      activeVersionId: 7,
    });
    const slider = screen.getByRole("slider");
    slider.focus();
    fireEvent.keyDown(slider, { key: "ArrowRight" });
    await waitFor(() => expect(api.listVersions).toHaveBeenCalled());
    return rendered;
  }

  it("shows what the check found on an edit it kept", async () => {
    await edit(
      {
        ok: true, error: null, version: { ...version({ gap: 6 }), id: 8 },
        assembly_findings: { failures: [OVERLAP], unchecked: [] },
      },
      8,
    );
    const box = await screen.findByRole("group", { name: "Assembly check" });
    within(box).getByText(OVERLAP);
    within(box).getByText("On the build that was kept.");
  });

  it("says why an edit was not kept", async () => {
    await edit(
      {
        ok: false, error: `assembly: ${OVERLAP}`, version: { ...version({ gap: 6 }), id: 8, ok: false },
        assembly_findings: { failures: [OVERLAP], unchecked: [] },
      },
      7,
    );
    expect((await screen.findByRole("alert")).textContent).toContain(OVERLAP);
    const box = screen.getByRole("group", { name: "Assembly check" });
    within(box).getByText("On a build that was not kept.");
  });

  it("keeps an edit's findings off another version", async () => {
    // Kept as version 8, while version 7 is the one on screen.
    await edit(
      {
        ok: true, error: null, version: { ...version({ gap: 6 }), id: 8 },
        assembly_findings: { failures: [OVERLAP], unchecked: [] },
      },
      7,
    );
    await waitFor(() => expect(screen.queryByText("rebuilding…")).toBeNull());
    expect(screen.queryByRole("group", { name: "Assembly check" })).toBeNull();
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
