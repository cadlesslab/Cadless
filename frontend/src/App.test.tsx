/** The app shell's two layouts.
 *
 * The desktop cases are here because the narrow work is easy to make global by
 * accident, and nothing else in the suite renders the shell. The localStorage
 * case is the one that is not obvious: the persisted layout belongs to the
 * desktop, and a phone visit must not overwrite it. */
import { fireEvent, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { renderWithProviders } from "./test/utils";

// three.js in jsdom, and a bootstrap that would reach the network. Neither is
// what these tests are about.
vi.mock("./viewport/Viewport", () => ({ Viewport: () => <div data-testid="viewport" /> }));
vi.mock("./useApp", () => ({
  useApp: () => ({ bootstrap: vi.fn(), openShared: vi.fn() }),
  useProjectActions: () => ({ selectProject: vi.fn(), refreshProjects: vi.fn() }),
}));

/** Answer every media query with `matches`, the way a viewport of that width
 *  would. Replaced wholesale rather than tweaked, per the setup file's note. */
function atWidth(matches: boolean) {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }));
}

const LAYOUT_KEY = "cadless-panels";

beforeEach(() => {
  localStorage.clear();
  vi.unstubAllGlobals();
});

describe("App shell — wide", () => {
  it("keeps the chat in a column of its own, with its resize handle", () => {
    atWidth(false);
    const { container } = renderWithProviders(<App />);
    const chat = container.querySelector(".side-col.right");
    expect(chat).not.toBeNull();
    expect(chat).not.toHaveAttribute("hidden");
    expect(screen.getByLabelText("Resize chat panel")).toBeInTheDocument();
    // The drawer control belongs to the narrow layout and to nothing else.
    expect(screen.queryByRole("button", { name: "Open chat" })).toBeNull();
  });
});

describe("App shell — narrow", () => {
  it("starts with the chat out of the way and a control to bring it back", () => {
    atWidth(true);
    const { container } = renderWithProviders(<App />);
    expect(container.querySelector(".side-col.right")).toHaveAttribute("hidden");
    expect(screen.getByRole("button", { name: "Open chat" })).toBeInTheDocument();
    // Nothing may take width from the canvas at this size, the resize handle
    // included — there is no second column left to size.
    expect(screen.queryByLabelText("Resize chat panel")).toBeNull();
  });

  it("opens the chat over the canvas, and closes it again", () => {
    atWidth(true);
    const { container } = renderWithProviders(<App />);
    fireEvent.click(screen.getByRole("button", { name: "Open chat" }));

    const chat = container.querySelector(".side-col.right");
    expect(chat).not.toHaveAttribute("hidden");
    // The canvas stays mounted underneath rather than being swapped out; the
    // drawer floats over it.
    expect(screen.getByTestId("viewport")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Collapse chat" }));
    expect(container.querySelector(".side-col.right")).toHaveAttribute("hidden");
  });

  it("keeps the chat mounted while it is closed", () => {
    atWidth(true);
    const { container } = renderWithProviders(<App />);
    // Unmounting would be simpler and would throw away a half-typed prompt,
    // because the composer's draft is the chat panel's own state.
    expect(container.querySelector(".side-col.right .composer")).not.toBeNull();
  });

  it("does not write the phone's layout over the desktop's", () => {
    atWidth(true);
    renderWithProviders(<App />);
    fireEvent.click(screen.getByRole("button", { name: "Open chat" }));
    fireEvent.click(screen.getByRole("button", { name: "Collapse chat" }));
    // The drawer is ordinary component state on purpose. Driving it through the
    // persisted `rightCollapsed` would leave the desktop collapsed on the next
    // visit from a laptop.
    expect(localStorage.getItem(LAYOUT_KEY)).toBeNull();
  });
});
