/** The seam a build uses to offer a panel this tree does not contain.
 *
 * The rail used to name its panels in a `switch`, which meant a build could
 * only differ from this one by editing the rail. These are the cases that say
 * it no longer has to: a panel handed in afterwards is drawn like any other,
 * and an id this build has no panel for opens nothing rather than failing.
 */
import { fireEvent, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "../test/utils";
import { LeftRail, RailFlyout } from "./LeftRail";
import { panelFor, registerPanel, registeredPanels, unregisterPanel } from "./registry";

const ADDED = "shipped-separately";

afterEach(() => {
  unregisterPanel(ADDED);
});

describe("the panel registry", () => {
  it("draws a panel registered after the built-ins, without the rail naming it", () => {
    registerPanel(ADDED, {
      label: "Shipped Separately",
      icon: <svg data-testid="added-icon" />,
      render: () => <p>Drawn by a panel this tree does not contain.</p>,
    });
    const onSelect = vi.fn();

    renderWithProviders(<LeftRail active={null} onSelect={onSelect} />);
    fireEvent.click(screen.getByRole("button", { name: "Shipped Separately" }));

    expect(onSelect).toHaveBeenCalledWith(ADDED);
  });

  it("renders the added panel into the flyout", () => {
    registerPanel(ADDED, {
      label: "Shipped Separately",
      icon: <svg />,
      render: () => <p>Drawn by a panel this tree does not contain.</p>,
    });

    renderWithProviders(<RailFlyout id={ADDED} width={320} onResize={vi.fn()} onClose={vi.fn()} />);

    expect(screen.getByText(/does not contain/)).toBeInTheDocument();
  });

  it("keeps the built-in panels, which register at module load", () => {
    // The rail is drawn from the registry now, so this is what says the
    // built-ins actually arrive there rather than the rail being empty.
    const ids = registeredPanels().map((panel) => panel.id);

    expect(ids).toContain("projects");
    expect(ids).toContain("settings");
    // Registration order is rail order: projects sits at the top.
    expect(ids[0]).toBe("projects");
  });

  it("opens nothing for an id this build has no panel for", () => {
    // An id is an unchecked string now, and a build ships whichever panels it
    // registered — so a flyout can be asked for an id nothing answers to, and
    // it must draw empty rather than fail to render at all.
    expect(panelFor("never-registered")).toBeUndefined();

    const { container } = renderWithProviders(
      <RailFlyout id="never-registered" width={320} onResize={vi.fn()} onClose={vi.fn()} />,
    );

    expect(container.querySelector(".flyout-body")).toBeEmptyDOMElement();
  });

  it("lets a build replace a panel it did not write", () => {
    // First-registration-wins would make the outcome depend on module load
    // order, which nothing controls.
    registerPanel(ADDED, { label: "First", icon: <svg />, render: () => <p>first</p> });
    registerPanel(ADDED, { label: "Second", icon: <svg />, render: () => <p>second</p> });

    renderWithProviders(<RailFlyout id={ADDED} width={320} onResize={vi.fn()} onClose={vi.fn()} />);

    expect(screen.getByText("second")).toBeInTheDocument();
    expect(screen.queryByText("first")).toBeNull();
  });
});
