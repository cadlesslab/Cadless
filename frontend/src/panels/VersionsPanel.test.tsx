import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Version } from "../api";
import * as api from "../api";
import { renderWithProviders } from "../test/utils";
import { VersionsPanel } from "./VersionsPanel";

vi.mock("../api", async (orig) => {
  const actual = await orig<typeof import("../api")>();
  return {
    ...actual,
    rerunVersion: vi.fn(async () => ({ ok: true, error: null, version: {} as Version })),
    listVersions: vi.fn(async () => []),
    listProjects: vi.fn(async () => []),
    branchFromVersion: vi.fn(async (_pid: number, vid: number) => ({
      id: 42, name: "Origin (branch)", created_at: "", updated_at: "",
      current_version_id: vid, branched_from_version_id: vid,
    })),
    getMessages: vi.fn(async () => []),
  };
});

function version(
  id: number, prompt: string, parent: number | null = null,
  planStep: number | null = null,
): Version {
  return {
    id, project_id: 1, prompt, code: "result=...", ok: true, error: null,
    volume: id * 100, bbox: [id, id, id], created_at: "", parameters: {},
    parent_version_id: parent, plan_step: planStep, artifacts: [],
  };
}

const project = { id: 1, name: "P", created_at: "", updated_at: "", current_version_id: 2 };

afterEach(() => vi.clearAllMocks());

describe("VersionsPanel", () => {
  it("lists versions newest-first with current badge and lineage", () => {
    renderWithProviders(<VersionsPanel />, {
      projects: [project],
      activeProjectId: 1,
      versions: [version(1, "first"), version(2, "second", 1)],
      activeVersionId: 2,
    });
    const ids = screen.getAllByText(/^v\d+$/).map((n) => n.textContent);
    expect(ids).toEqual(["v2", "v1"]);
    expect(screen.getByText("current")).toBeInTheDocument();
    expect(screen.getByText("↳ v1")).toBeInTheDocument(); // v2 refined from v1
  });

  it("narrates the plan step a version was written under, when present", () => {
    renderWithProviders(<VersionsPanel />, {
      projects: [project],
      activeProjectId: 1,
      versions: [version(1, "first"), version(2, "second", 1, 3)],
      activeVersionId: 2,
    });
    // v2 carries plan_step=3 → narrated; v1 has none → no step badge for it.
    const step = screen.getByText("step 3");
    expect(step).toBeInTheDocument();
    expect(step.getAttribute("title")).toBe("written at plan step 3");
    expect(screen.queryByText("step 1")).not.toBeInTheDocument();
  });

  it("recalls a version's prompt into the composer", () => {
    const { store } = renderWithProviders(<VersionsPanel />, {
      projects: [project], activeProjectId: 1, versions: [version(1, "a cube")], activeVersionId: 1,
    });
    fireEvent.click(screen.getByRole("button", { name: "Use v1 prompt" }));
    expect(store.get().recalledPrompt).toBe("a cube");
  });

  it("renames (annotates) a version", () => {
    renderWithProviders(<VersionsPanel />, {
      projects: [project], activeProjectId: 1, versions: [version(1, "a cube")], activeVersionId: 1,
    });
    fireEvent.click(screen.getByRole("button", { name: "Rename v1" }));
    fireEvent.change(screen.getByLabelText("Label"), { target: { value: "Mounting plate" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(screen.getByText("Mounting plate")).toBeInTheDocument();
  });

  it("enables Compare with two selected and shows a metrics diff", () => {
    renderWithProviders(<VersionsPanel />, {
      projects: [project], activeProjectId: 1,
      versions: [version(1, "first"), version(2, "second", 1)], activeVersionId: 2,
    });
    const compareBtn = screen.getByRole("button", { name: "Compare" });
    expect(compareBtn).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox", { name: "Compare v1" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Compare v2" }));
    expect(compareBtn).toBeEnabled();
    fireEvent.click(compareBtn);
    expect(screen.getByText(/Compare v1 vs v2/)).toBeInTheDocument();
    expect(screen.getByText("Volume (mm³)")).toBeInTheDocument();
  });

  it("re-runs a version", async () => {
    renderWithProviders(<VersionsPanel />, {
      projects: [project], activeProjectId: 1, versions: [version(1, "first")], activeVersionId: 1,
    });
    fireEvent.click(screen.getByRole("button", { name: "Re-run v1" }));
    await waitFor(() => expect(api.rerunVersion).toHaveBeenCalledWith(1));
  });

  it("hides the re-run action on read-only catalog projects (#31)", () => {
    renderWithProviders(<VersionsPanel />, {
      projects: [{ ...project, is_catalog: true }],
      activeProjectId: 1,
      versions: [version(1, "first")],
      activeVersionId: 1,
    });
    expect(screen.queryByRole("button", { name: "Re-run v1" })).not.toBeInTheDocument();
    // other actions remain available on catalog items
    expect(screen.getByRole("button", { name: "Branch from v1" })).toBeInTheDocument();
  });

  it("branches from a version and switches to the new line", async () => {
    const { store } = renderWithProviders(<VersionsPanel />, {
      projects: [project], activeProjectId: 1,
      versions: [version(1, "first"), version(2, "second", 1)], activeVersionId: 2,
    });
    fireEvent.click(screen.getByRole("button", { name: "Branch from v1" }));
    await waitFor(() => expect(api.branchFromVersion).toHaveBeenCalledWith(1, 1));
    // switches the active line to the newly forked project
    await waitFor(() => expect(store.get().activeProjectId).toBe(42));
  });
});
