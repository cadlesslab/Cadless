/** The app shell's two layouts, and the crossing between them.
 *
 * The desktop cases are here because the narrow work is easy to make global by
 * accident, and nothing else in the suite renders the shell. The localStorage
 * case is the one that is not obvious: the persisted layout belongs to the
 * desktop, and a phone visit must not overwrite it. The crossing cases are here
 * because that is where the three-way state — narrow, collapsed, drawer open —
 * can disagree with itself. */
import { act, fireEvent, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { renderWithProviders } from "./test/utils";
import { NARROW_QUERY } from "./useNarrow";

// three.js in jsdom, and a bootstrap that would reach the network. Neither is
// what these tests are about.
vi.mock("./viewport/Viewport", () => ({ Viewport: () => <div data-testid="viewport" /> }));
vi.mock("./useApp", () => ({
  useApp: () => ({ bootstrap: vi.fn(), openShared: vi.fn() }),
  useProjectActions: () => ({ selectProject: vi.fn(), refreshProjects: vi.fn() }),
}));

/** A matchMedia that answers for the width query only, and can be made to
 *  change the way a browser does. Keyed on the query rather than answering
 *  `matches` to everything, so it stays honest when something else starts
 *  asking about `prefers-reduced-motion` or the colour scheme. */
function stubWidth(narrow: boolean) {
  const listeners = new Set<(e: MediaQueryListEvent) => void>();
  vi.stubGlobal("matchMedia", (query: string) => ({
    get matches() {
      return query === NARROW_QUERY && narrow;
    },
    media: query,
    onchange: null,
    addEventListener: (_: string, fn: (e: MediaQueryListEvent) => void) =>
      query === NARROW_QUERY && listeners.add(fn),
    removeEventListener: (_: string, fn: (e: MediaQueryListEvent) => void) => listeners.delete(fn),
    dispatchEvent: () => false,
  }));
  return {
    resize(next: boolean) {
      narrow = next;
      act(() => {
        for (const fn of listeners) fn({ matches: next } as MediaQueryListEvent);
      });
    },
  };
}

const LAYOUT_KEY = "cadless-panels";
const chatOf = (c: HTMLElement) => c.querySelector(".side-col.right");

beforeEach(() => {
  localStorage.clear();
  vi.unstubAllGlobals();
});

describe("App shell — wide", () => {
  it("keeps the chat in a column of its own, with its resize handle", () => {
    stubWidth(false);
    const { container } = renderWithProviders(<App />);
    const chat = chatOf(container);
    expect(chat).not.toBeNull();
    expect(chat).not.toHaveAttribute("hidden");
    expect(chat).not.toHaveClass("drawer");
    expect(screen.getByLabelText("Resize chat panel")).toBeInTheDocument();
    // The drawer control belongs to the narrow layout and to nothing else.
    expect(screen.queryByRole("button", { name: "Open chat" })).toBeNull();
    // And the close control names what it actually does here.
    expect(screen.getByRole("button", { name: "Collapse chat" })).toBeInTheDocument();
  });
});

describe("App shell — narrow", () => {
  it("starts with the chat out of the way and a control to bring it back", () => {
    stubWidth(true);
    const { container } = renderWithProviders(<App />);
    expect(chatOf(container)).toHaveAttribute("hidden");
    expect(screen.getByRole("button", { name: "Open chat" })).toBeInTheDocument();
    // Nothing may take width from the canvas at this size, the resize handle
    // included — there is no second column left to size.
    expect(screen.queryByLabelText("Resize chat panel")).toBeNull();
  });

  it("opens the chat over the canvas, and closes it again", () => {
    stubWidth(true);
    const { container } = renderWithProviders(<App />);
    fireEvent.click(screen.getByRole("button", { name: "Open chat" }));

    expect(chatOf(container)).not.toHaveAttribute("hidden");
    // The canvas stays mounted underneath rather than being swapped out; the
    // drawer floats over it.
    expect(screen.getByTestId("viewport")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Close chat" }));
    expect(chatOf(container)).toHaveAttribute("hidden");
  });

  it("closes on Escape", () => {
    stubWidth(true);
    const { container } = renderWithProviders(<App />);
    fireEvent.click(screen.getByRole("button", { name: "Open chat" }));
    expect(chatOf(container)).not.toHaveAttribute("hidden");
    fireEvent.keyDown(document, { key: "Escape" });
    expect(chatOf(container)).toHaveAttribute("hidden");
  });

  it("keeps the chat mounted while it is closed", () => {
    stubWidth(true);
    const { container } = renderWithProviders(<App />);
    // Unmounting would be simpler and would throw away a half-typed prompt,
    // because the composer's draft is the chat panel's own state.
    expect(container.querySelector(".side-col.right .composer")).not.toBeNull();
  });

  it("does not write the phone's layout over the desktop's", () => {
    stubWidth(true);
    renderWithProviders(<App />);
    fireEvent.click(screen.getByRole("button", { name: "Open chat" }));
    fireEvent.click(screen.getByRole("button", { name: "Close chat" }));
    // The drawer is ordinary component state on purpose. Driving it through the
    // persisted `rightCollapsed` would leave the desktop collapsed on the next
    // visit from a laptop.
    expect(localStorage.getItem(LAYOUT_KEY)).toBeNull();
  });

  it("ignores a collapsed desktop layout left in storage", () => {
    // A laptop collapsed the chat, then the same person opens the app on a
    // phone. The 40px strip is a wide-layout answer to a wide-layout problem;
    // offering it here would leave two controls doing the drawer's one job.
    localStorage.setItem(LAYOUT_KEY, JSON.stringify({ rightCollapsed: true }));
    stubWidth(true);
    const { container } = renderWithProviders(<App />);
    expect(chatOf(container)).not.toHaveClass("collapsed");
    expect(screen.queryByRole("button", { name: "Expand chat" })).toBeNull();
    expect(screen.getByRole("button", { name: "Open chat" })).toBeInTheDocument();
  });
});

describe("App shell — crossing the breakpoint", () => {
  it("hands the chat back to its own column when the window widens", () => {
    const media = stubWidth(true);
    const { container } = renderWithProviders(<App />);
    fireEvent.click(screen.getByRole("button", { name: "Open chat" }));

    media.resize(false);

    const chat = chatOf(container);
    // A drawer left open must not survive as a floating panel on the desktop,
    // and the handle has to come back with the column.
    expect(chat).not.toHaveAttribute("hidden");
    expect(chat).not.toHaveClass("drawer");
    expect(screen.getByLabelText("Resize chat panel")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Open chat" })).toBeNull();
  });

  it("puts the chat away again when the window narrows", () => {
    const media = stubWidth(false);
    const { container } = renderWithProviders(<App />);
    expect(chatOf(container)).not.toHaveAttribute("hidden");

    media.resize(true);

    expect(chatOf(container)).toHaveAttribute("hidden");
    expect(screen.getByRole("button", { name: "Open chat" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Resize chat panel")).toBeNull();
  });
});
