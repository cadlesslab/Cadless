import { fireEvent, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ChatEvent, MessageOut, Version } from "../api";
import * as api from "../api";
import { renderWithProviders } from "../test/utils";
import { ChatPanel } from "./ChatPanel";

// streamChat invokes onEvent with a quick error so the store populates and the
// live turn settles, without hitting the network refresh afterwards.
function failChat(_pid: number, _msg: string, onEvent: (e: ChatEvent) => void) {
  onEvent({ event: "tool_start", tool: "generate", label: "Modeling" });
  onEvent({
    event: "tool_progress",
    stage: { event: "stage", phase: "build", status: "error", attempt: 1, error: "boom" },
  });
  onEvent({ event: "error", detail: "boom" });
  return Promise.resolve();
}

vi.mock("../api", async (orig) => ({
  ...(await orig<typeof import("../api")>()),
  streamChat: vi.fn(failChat),
  listProjects: vi.fn(async () => []),
  listVersions: vi.fn(async () => []),
  getMessages: vi.fn(async () => []),
  cloneProject: vi.fn(async (_pid: number, name?: string) => ({
    id: 999, name: name ?? "copy", created_at: "", updated_at: "", current_version_id: null,
  })),
}));

const project = { id: 1, name: "P", created_at: "", updated_at: "", current_version_id: null };

function okVersion(): Version {
  return {
    id: 7, project_id: 1, prompt: "a cube", code: "result=...", ok: true, error: null,
    volume: 1, bbox: [1, 1, 1], created_at: "", parameters: {}, parent_version_id: null,
    plan_step: null, artifacts: [],
  };
}

function blockMessages(): MessageOut[] {
  return [
    { id: 1, seq: 1, role: "user", content: null, status: "ok", error: null, version_id: null, created_at: "", blocks: [{ kind: "text", text: "a cube" }] },
    { id: 2, seq: 2, role: "assistant", content: null, status: "ok", error: null, version_id: 7, created_at: "", blocks: [{ kind: "text", text: "Done — **here** it is" }] },
  ];
}

afterEach(() => vi.clearAllMocks());

describe("ChatPanel", () => {
  it("renders the panel header and aria-label as \"Cadless\"", () => {
    renderWithProviders(<ChatPanel />, { activeProjectId: 1, projects: [project] });
    expect(screen.getByRole("region", { name: "Cadless" })).toBeInTheDocument();
    expect(screen.queryByText("Chat")).not.toBeInTheDocument();
  });

  it("shows the active project title beside the Cadless brand", () => {
    const named = { ...project, name: "L-Shaped Mounting Bracket", is_catalog: true };
    renderWithProviders(<ChatPanel />, { activeProjectId: 1, projects: [named] });
    expect(screen.getByText("L-Shaped Mounting Bracket")).toBeInTheDocument();
    expect(screen.getByText("catalog")).toBeInTheDocument();
  });

  it("offers Customize on an open catalog project, cloning + switching to the copy (#22)", async () => {
    const catalog = { ...project, name: "Spur Gear", is_catalog: true };
    const { store } = renderWithProviders(<ChatPanel />, {
      activeProjectId: 1,
      projects: [catalog],
    });
    fireEvent.click(screen.getByRole("button", { name: "Customize" }));
    await screen.findByRole("button", { name: "Customize" }); // let the action settle
    expect(api.cloneProject).toHaveBeenCalledWith(1, "Spur Gear (copy)");
    await vi.waitFor(() => expect(store.get().activeProjectId).toBe(999));
  });

  it("drops the note and hands chat back once the clone is in place", async () => {
    const catalog = { ...project, name: "Spur Gear", is_catalog: true };
    const clone = { ...project, id: 999, name: "Spur Gear (copy)", is_catalog: false };
    // The refresh has to hand back the clone: with an empty project list the note
    // would vanish merely because nothing is active, which proves nothing about
    // the copy being editable.
    vi.mocked(api.listProjects).mockResolvedValueOnce([clone]);
    const { store } = renderWithProviders(<ChatPanel />, {
      activeProjectId: 1,
      projects: [catalog],
    });
    fireEvent.click(screen.getByRole("button", { name: "Customize" }));
    await vi.waitFor(() => expect(store.get().activeProjectId).toBe(999));
    expect(document.querySelector(".chat-readonly-note")).toBeNull();
    expect(screen.getByPlaceholderText(/Describe or refine your part/)).toBeEnabled();
  });

  it("hides Customize on ordinary (non-catalog) projects", () => {
    renderWithProviders(<ChatPanel />, { activeProjectId: 1, projects: [project] });
    expect(screen.queryByRole("button", { name: "Customize" })).toBeNull();
  });

  it("shows a based-on chip on clones that links back to the catalog item (#22)", async () => {
    const baseline = { ...project, id: 3, name: "Spur Gear", is_catalog: true };
    const clone = {
      ...project,
      id: 1,
      name: "Spur Gear (copy)",
      derived_from_project_id: 3,
      derived_from_name: "Spur Gear",
      derived_from_catalog_id: "spur-gear-1",
    };
    const { store } = renderWithProviders(<ChatPanel />, {
      activeProjectId: 1,
      projects: [clone, baseline],
    });
    const chip = screen.getByRole("button", { name: "Based on Spur Gear" });
    expect(chip).toHaveTextContent(/based on\s+Spur Gear/i);
    fireEvent.click(chip);
    await vi.waitFor(() => expect(store.get().activeProjectId).toBe(3));
  });

  it("enables Send only with text + active project, and streams a chat turn on click", () => {
    renderWithProviders(<ChatPanel />, { activeProjectId: 1, projects: [project] });
    const btn = screen.getByRole("button", { name: "Send" });
    expect(btn).toBeDisabled();
    fireEvent.change(screen.getByPlaceholderText(/Describe or refine your part/), { target: { value: "a 10mm cube" } });
    expect(btn).toBeEnabled();
    fireEvent.click(btn);
    // Forge is off by default => the turn does not opt in.
    expect(api.streamChat).toHaveBeenCalledWith(1, "a 10mm cube", expect.any(Function), expect.any(Object), false);
  });

  it("runs an example prompt from the empty state", () => {
    renderWithProviders(<ChatPanel />, { activeProjectId: 1, projects: [project] });
    fireEvent.click(screen.getByRole("button", { name: "Plate with hole" }));
    expect(api.streamChat).toHaveBeenCalledWith(1, expect.stringContaining("plate"), expect.any(Function), expect.any(Object), false);
  });

  it("opts the turn into forge mode when the Forge toggle is on", () => {
    renderWithProviders(<ChatPanel />, { activeProjectId: 1, projects: [project] });
    fireEvent.click(screen.getByRole("button", { name: /Forge/i }));
    fireEvent.change(screen.getByPlaceholderText(/Describe or refine your part/), { target: { value: "a cube" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(api.streamChat).toHaveBeenCalledWith(1, "a cube", expect.any(Function), expect.any(Object), true);
  });

  it("renders the block-based transcript with markdown and a result card", () => {
    renderWithProviders(<ChatPanel />, {
      activeProjectId: 1,
      projects: [project],
      versions: [okVersion()],
      activeVersionId: 7,
      messages: blockMessages(),
    });
    expect(screen.getByText("a cube")).toBeInTheDocument(); // user bubble
    expect(screen.getByText("here").tagName).toBe("STRONG"); // markdown bold
    expect(screen.getByText("v7").closest(".result-card")).not.toBeNull(); // result card
    // editable project => the result card offers Re-run
    expect(screen.getByRole("button", { name: "Re-run v7" })).toBeInTheDocument();
  });

  it("hides Re-run on transcript result cards of read-only catalog projects (#31)", () => {
    renderWithProviders(<ChatPanel />, {
      activeProjectId: 1,
      projects: [{ ...project, is_catalog: true }],
      versions: [okVersion()],
      activeVersionId: 7,
      messages: blockMessages(),
    });
    expect(screen.getByText("v7").closest(".result-card")).not.toBeNull();
    expect(screen.queryByRole("button", { name: "Re-run v7" })).toBeNull();
    // recall stays available on catalog transcripts
    expect(screen.getByRole("button", { name: "Use v7 prompt" })).toBeInTheDocument();
  });

  it("takes chat away on read-only catalog projects rather than letting it 403", () => {
    renderWithProviders(<ChatPanel />, {
      activeProjectId: 1,
      projects: [{ ...project, is_catalog: true }],
    });
    expect(screen.getByPlaceholderText(/Describe or refine your part/)).toBeDisabled();
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
    // The empty-state example prompts bypass the composer, so they must lose the
    // affordance too — not merely be inert on click.
    const example = screen.getByRole("button", { name: "Plate with hole" });
    expect(example).toBeDisabled();
    fireEvent.click(example);
    expect(api.streamChat).not.toHaveBeenCalled();
    // ...and the way forward is still on offer.
    expect(screen.getByRole("button", { name: "Customize" })).toBeInTheDocument();
  });

  it("puts the way forward beside the blocked composer, not up in the header", () => {
    renderWithProviders(<ChatPanel />, {
      activeProjectId: 1,
      projects: [{ ...project, is_catalog: true }],
    });
    const customize = screen.getByRole("button", { name: "Customize" });
    // The note says why the composer is dead...
    const note = customize.closest(".chat-readonly-note");
    expect(note).not.toBeNull();
    expect(note).toHaveTextContent(/read-only/i);
    // ...and sits immediately before the composer it is explaining, so the
    // reason and the remedy land where the user actually got stuck.
    expect(note?.nextElementSibling).toHaveClass("composer");
    // The header keeps the badge but no longer duplicates the action.
    expect(customize.closest(".chat-head")).toBeNull();
    expect(screen.getByText("catalog")).toBeInTheDocument();
  });

  it("leaves ordinary projects without the read-only note", () => {
    renderWithProviders(<ChatPanel />, { activeProjectId: 1, projects: [project] });
    expect(document.querySelector(".chat-readonly-note")).toBeNull();
  });

  it("surfaces a streaming error and retries the last turn", async () => {
    renderWithProviders(<ChatPanel />, { activeProjectId: 1, projects: [project] });
    fireEvent.change(screen.getByPlaceholderText(/Describe or refine your part/), { target: { value: "a cube" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(api.streamChat).toHaveBeenCalledTimes(1);
    expect(await screen.findByText("boom")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(api.streamChat).toHaveBeenCalledTimes(2);
  });

  it("shows a Stop button that aborts the in-flight turn", () => {
    let signal: AbortSignal | undefined;
    vi.mocked(api.streamChat).mockImplementationOnce((_p, _m, _on, sig) => {
      signal = sig;
      return new Promise(() => {}); // never settles until aborted
    });
    renderWithProviders(<ChatPanel />, { activeProjectId: 1, projects: [project] });
    fireEvent.change(screen.getByPlaceholderText(/Describe or refine your part/), { target: { value: "a cube" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    const stop = screen.getByRole("button", { name: "Stop" });
    expect(signal?.aborted).toBe(false);
    fireEvent.click(stop);
    expect(signal?.aborted).toBe(true);
  });
});
