import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "../test/utils";
import { LeftRail, RailFlyout } from "./LeftRail";
import { panelFor } from "./registry";

describe("LeftRail", () => {
  it("renders the panel icons and reports selection", () => {
    const onSelect = vi.fn();
    renderWithProviders(<LeftRail active={null} onSelect={onSelect} />);
    for (const label of [
      "Projects",
      "Catalog",
      "Import",
      "View",
      "Details",
      "Parameters",
      "History",
    ]) {
      expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
    }
    fireEvent.click(screen.getByRole("button", { name: "Catalog" }));
    expect(onSelect).toHaveBeenCalledWith("catalog");
  });

  // Relocated from the panel that used to draw the import control as one of its
  // sections. What the import section *does* is covered by ImportPanel.test.tsx,
  // which renders it on its own; what only the rail can answer is that it is
  // still offered, and offered with no account. A package handed over directly
  // must not inherit a sign-in requirement by sharing a flyout with something
  // that has one — which is why nothing here stubs any remote API at all.
  //
  // `getByRole` is also what pins *exactly once*: it throws on a second match,
  // so a build that drew Import twice would fail here rather than pass quietly.
  it("offers the local import as its own entry, without signing in", async () => {
    const onSelect = vi.fn();
    renderWithProviders(<LeftRail active={null} onSelect={onSelect} />);

    fireEvent.click(screen.getByRole("button", { name: "Import" }));
    expect(onSelect).toHaveBeenCalledWith("import");

    renderWithProviders(<>{panelFor("import")?.render()}</>);
    expect(await screen.findByLabelText(/package file/i)).toBeInTheDocument();
  });

  it("renders the CadlessIcon spark as the brand mark", () => {
    const { container } = renderWithProviders(<LeftRail active={null} onSelect={() => {}} />);
    const brand = container.querySelector(".rail-brand");
    expect(brand).not.toBeNull();
    expect(brand?.querySelector("svg path")).not.toBeNull();
    expect(brand?.textContent).not.toContain("◆");
  });

  it("marks the active icon as pressed", () => {
    renderWithProviders(<LeftRail active="history" onSelect={() => {}} />);
    expect(screen.getByRole("button", { name: "History" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Projects" })).toHaveAttribute("aria-pressed", "false");
  });
});

describe("RailFlyout", () => {
  function open(onClose: () => void) {
    return renderWithProviders(
      <RailFlyout id="details" width={320} onResize={() => {}} onClose={onClose} />,
    );
  }

  it("closes on Escape when nothing is floating over it", () => {
    const onClose = vi.fn();
    open(onClose);

    fireEvent.keyDown(document, { key: "Escape" });

    expect(onClose).toHaveBeenCalled();
  });

  it("still closes on Escape while a tooltip happens to be showing", () => {
    // A tooltip is a label, not something to dismiss. What claims Escape says
    // so itself — see HelpPopover — and a check for "is anything floating"
    // here would have counted every rail tooltip as a reason to stay open.
    const onClose = vi.fn();
    open(onClose);
    const floating = document.createElement("div");
    floating.setAttribute("data-radix-popper-content-wrapper", "");
    document.body.appendChild(floating);

    fireEvent.keyDown(document, { key: "Escape" });

    expect(onClose).toHaveBeenCalled();
    floating.remove();
  });
});
