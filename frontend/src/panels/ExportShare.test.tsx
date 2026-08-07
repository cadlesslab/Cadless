import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Version } from "../api";
import { ToastProvider } from "../components";
import { ExportShare } from "./ExportShare";

function version(kinds: string[]): Version {
  return {
    id: 5, project_id: 2, prompt: "p", code: null, ok: true, error: null,
    volume: 1, bbox: [1, 1, 1], created_at: "", parameters: {}, parent_version_id: null,
    plan_step: null,
    artifacts: kinds.map((k) => ({ kind: k as Version["artifacts"][number]["kind"], bytes: 1 })),
  };
}

function renderShare(v: Version) {
  return render(
    <ToastProvider>
      <ExportShare version={v} />
    </ToastProvider>,
  );
}

beforeEach(() => {
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: vi.fn(() => "blob:x"), revokeObjectURL: vi.fn() }));
});
afterEach(() => vi.unstubAllGlobals());

describe("ExportShare", () => {
  it("shows only the formats present on the version", () => {
    renderShare(version(["step", "glb"]));
    expect(screen.getByRole("button", { name: "STEP" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "GLB" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "STL" })).not.toBeInTheDocument();
  });

  it("downloads a format via fetch + blob and toasts success", async () => {
    const fetchFn = vi.fn().mockResolvedValue({ ok: true, blob: async () => new Blob(["x"]) });
    vi.stubGlobal("fetch", fetchFn);
    renderShare(version(["step", "stl", "obj", "glb"]));
    fireEvent.click(screen.getByRole("button", { name: "STL" }));
    await waitFor(() => expect(fetchFn).toHaveBeenCalledWith(expect.stringContaining("/versions/5/artifacts/stl")));
    await waitFor(() => expect(screen.getByText("STL downloaded")).toBeInTheDocument());
  });

  it("shows an error toast when an artifact is missing", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 404 }));
    renderShare(version(["obj"]));
    fireEvent.click(screen.getByRole("button", { name: "OBJ" }));
    await waitFor(() => expect(screen.getByText("Couldn't download OBJ")).toBeInTheDocument());
  });

  it("copies a share link", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    renderShare(version(["step"]));
    fireEvent.click(screen.getByRole("button", { name: "↗ Share" }));
    // Project id in the path, version pinned with ?v=.
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(expect.stringContaining("/2?v=5")));
  });
});
