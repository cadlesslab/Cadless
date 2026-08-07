import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { HelpPopover } from "./HelpPopover";

function renderHelp() {
  return render(
    <div>
      <h3>Import a package</h3>
      <HelpPopover label="About importing a package" title="Importing a package">
        A package handed over directly went through no upload check.
      </HelpPopover>
      <button>Import</button>
    </div>,
  );
}

describe("HelpPopover", () => {
  it("keeps the note behind the mark until it is asked for", () => {
    renderHelp();

    expect(screen.getByRole("button", { name: "About importing a package" })).toBeInTheDocument();
    expect(screen.queryByText(/went through no upload check/)).toBeNull();
  });

  it("shows the note when the mark is used", () => {
    renderHelp();

    fireEvent.click(screen.getByRole("button", { name: "About importing a package" }));

    expect(screen.getByText("Importing a package")).toBeInTheDocument();
    expect(screen.getByText(/went through no upload check/)).toBeInTheDocument();
  });

  it("lets Escape put the note away", () => {
    renderHelp();
    fireEvent.click(screen.getByRole("button", { name: "About importing a package" }));

    fireEvent.keyDown(document, { key: "Escape" });

    expect(screen.queryByText(/went through no upload check/)).toBeNull();
  });

  it("opens for a passing mouse and closes when it leaves", () => {
    renderHelp();
    const mark = screen.getByRole("button", { name: "About importing a package" });

    // `pointerover`, not `pointerenter`: React makes enter/leave out of the
    // bubbling pair, and the non-bubbling ones never reach it.
    fireEvent.pointerOver(mark, { pointerType: "mouse" });
    expect(screen.getByText(/went through no upload check/)).toBeInTheDocument();

    fireEvent.pointerOut(mark, { pointerType: "mouse" });
    expect(screen.queryByText(/went through no upload check/)).toBeNull();
  });

  it("does not take the focus away from what someone was doing when it only hovered", () => {
    renderHelp();
    const mark = screen.getByRole("button", { name: "About importing a package" });

    // `pointerover`, not `pointerenter`: React makes enter/leave out of the
    // bubbling pair, and the non-bubbling ones never reach it.
    fireEvent.pointerOver(mark, { pointerType: "mouse" });

    // Opened by a mouse that happened to pass over: the card is readable where
    // it is, and moving the focus into it would strand a keyboard somewhere
    // nobody asked to go.
    expect(document.activeElement).not.toBe(screen.getByRole("dialog"));
    expect(screen.getByRole("dialog").contains(document.activeElement)).toBe(false);
  });

  it("keeps Escape to itself so nothing behind it also closes", () => {
    // The panel this opens inside closes on Escape too, listening on the
    // document. One press should put the card away and leave the panel.
    const behind = vi.fn();
    document.addEventListener("keydown", behind);
    renderHelp();
    fireEvent.click(screen.getByRole("button", { name: "About importing a package" }));

    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });

    expect(screen.queryByText(/went through no upload check/)).toBeNull();
    expect(behind).not.toHaveBeenCalled();
    document.removeEventListener("keydown", behind);
  });

  it("gives the focus back to the mark when a card that had it closes", async () => {
    renderHelp();
    const mark = screen.getByRole("button", { name: "About importing a package" });

    fireEvent.click(mark);
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });

    // Otherwise the next Tab starts again at the top of the document, a long
    // way from the control the card was explaining. Awaited because the focus
    // is settled after the card has gone, not while it is going.
    await waitFor(() => expect(document.activeElement).toBe(mark));
  });

  it("leaves the focus where it was when the card only hovered", () => {
    renderHelp();
    const elsewhere = screen.getByRole("button", { name: "Import" });
    elsewhere.focus();
    const mark = screen.getByRole("button", { name: "About importing a package" });

    fireEvent.pointerOver(mark, { pointerType: "mouse" });
    fireEvent.pointerOut(mark, { pointerType: "mouse" });

    expect(document.activeElement).toBe(elsewhere);
  });

  it("names the card so it is not announced as an unnamed dialog", () => {
    renderHelp();
    fireEvent.click(screen.getByRole("button", { name: "About importing a package" }));

    expect(screen.getByRole("dialog", { name: "Importing a package" })).toBeInTheDocument();
  });

  it("floats in the wrapper the rail treats as part of the panel", () => {
    // The rail closes its panel on a click outside, and counts this wrapper as
    // inside — otherwise reading a card would dismiss what it explains. Said
    // here rather than there: Radix produces it, so a rename shows up here.
    renderHelp();
    expect(document.querySelector("[data-radix-popper-content-wrapper]")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "About importing a package" }));

    expect(document.querySelector("[data-radix-popper-content-wrapper]")).not.toBeNull();
  });

  it("stays open after a click even when the mouse moves off", () => {
    renderHelp();
    const mark = screen.getByRole("button", { name: "About importing a package" });

    fireEvent.click(mark);
    fireEvent.pointerOut(mark, { pointerType: "mouse" });

    expect(screen.getByText(/went through no upload check/)).toBeInTheDocument();
  });
});
