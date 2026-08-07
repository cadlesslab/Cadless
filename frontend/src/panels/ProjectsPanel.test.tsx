import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as api from "../api";
import { renderWithProviders } from "../test/utils";
import { ProjectsPanel } from "./ProjectsPanel";

vi.mock("../api", async (orig) => {
  const actual = await orig<typeof import("../api")>();
  return {
    ...actual,
    createProject: vi.fn(async (name: string) => ({
      id: 2, name, created_at: "", updated_at: "", current_version_id: null,
    })),
    listProjects: vi.fn(async () => []),
    listVersions: vi.fn(async () => []),
  };
});

const alpha = { id: 1, name: "Alpha", created_at: "", updated_at: "", current_version_id: null };

afterEach(() => vi.clearAllMocks());

describe("ProjectsPanel", () => {
  it("lists projects and marks the active one", () => {
    renderWithProviders(<ProjectsPanel />, { projects: [alpha], activeProjectId: 1 });
    expect(screen.getByRole("button", { name: "Alpha" })).toHaveAttribute("aria-current", "true");
  });

  it("selects a project on click", () => {
    const beta = { id: 2, name: "Beta", created_at: "", updated_at: "", current_version_id: null };
    const { store } = renderWithProviders(<ProjectsPanel />, {
      projects: [alpha, beta],
      activeProjectId: 1,
    });
    fireEvent.click(screen.getByRole("button", { name: "Beta" }));
    expect(store.get().activeProjectId).toBe(2);
  });

  it("creates a project through the modal", async () => {
    renderWithProviders(<ProjectsPanel />, { projects: [alpha], activeProjectId: 1 });
    fireEvent.click(screen.getByRole("button", { name: "New" }));
    fireEvent.change(screen.getByLabelText("Project name"), { target: { value: "Gamma" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    await waitFor(() => expect(api.createProject).toHaveBeenCalledWith("Gamma"));
  });

  it("shows the empty state with no projects", () => {
    renderWithProviders(<ProjectsPanel />, { projects: [] });
    expect(screen.getByText("No projects yet.")).toBeInTheDocument();
  });

  it("offers neither rename nor delete on a catalog item", () => {
    const item = { ...alpha, id: 3, name: "Catalog Item", is_catalog: true };
    renderWithProviders(<ProjectsPanel />, { projects: [alpha, item], activeProjectId: 1 });

    expect(screen.queryByRole("button", { name: "Rename Catalog Item" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Delete Catalog Item" })).toBeNull();
    // The user's own project still has both, so this hid the right row.
    expect(screen.getByRole("button", { name: "Rename Alpha" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Delete Alpha" })).toBeInTheDocument();
  });
});
