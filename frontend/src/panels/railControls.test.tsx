/** The rail's bottom seam.
 *
 * The case that matters most is the empty one: every build that registers
 * nothing — which is every local build — must draw exactly the rail it drew
 * before this registry existed. A seam is supposed to be invisible until
 * something fills it. */
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { LeftRail } from "./LeftRail";
import {
  registerRailControl,
  registeredRailControls,
  unregisterRailControl,
} from "./railControls";
import { renderWithProviders } from "../test/utils";

afterEach(() => {
  for (const { id } of registeredRailControls()) unregisterRailControl(id);
});

describe("the rail-bottom registry", () => {
  it("is empty until something registers", () => {
    expect(registeredRailControls()).toEqual([]);
  });

  it("answers in registration order", () => {
    registerRailControl("second", { render: () => null });
    registerRailControl("first", { render: () => null });
    // Insertion order, not alphabetical: which control sits highest is a
    // property of who registered when, the same rule panels follow.
    expect(registeredRailControls().map(({ id }) => id)).toEqual(["second", "first"]);
  });

  it("replaces rather than refusing a second registration", () => {
    registerRailControl("account", { render: () => <span>first</span> });
    registerRailControl("account", { render: () => <span>second</span> });

    const controls = registeredRailControls();
    expect(controls).toHaveLength(1);
    render(<>{controls[0]?.entry.render()}</>);
    expect(screen.getByText("second")).toBeInTheDocument();
  });

  it("lets a control be withdrawn again", () => {
    registerRailControl("account", { render: () => null });
    unregisterRailControl("account");
    expect(registeredRailControls()).toEqual([]);
  });
});

describe("the rail's bottom section", () => {
  it("draws nothing extra when nothing is registered", () => {
    const { container } = renderWithProviders(<LeftRail active={null} onSelect={() => {}} />);
    const bottom = container.querySelector(".rail-bottom");
    expect(bottom).not.toBeNull();
    expect(bottom?.querySelectorAll(".rail-control")).toHaveLength(0);
    // The two the app owns, and only those.
    expect(screen.getByRole("button", { name: "Toggle theme" })).toBeInTheDocument();
  });

  it("draws a registered control above the two the app owns", () => {
    registerRailControl("account", {
      render: () => <button type="button">Account</button>,
    });

    const { container } = renderWithProviders(<LeftRail active={null} onSelect={() => {}} />);
    const bottom = container.querySelector(".rail-bottom");
    expect(bottom?.querySelectorAll(".rail-control")).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Account" })).toBeInTheDocument();

    // Above, not below: help and the theme stay at the foot of the rail
    // whatever a build adds, so their position does not move with the count.
    const order = [...(bottom?.children ?? [])];
    const account = order.findIndex((el) => el.classList.contains("rail-control"));
    const themeButton = screen.getByRole("button", { name: "Toggle theme" });
    const theme = order.findIndex((el) => el.contains(themeButton));
    expect(account).toBeLessThan(theme);
  });

  it("keeps the rail's own controls out of the registry's reach", () => {
    // A build cannot replace help or the theme by registering over them: they
    // are drawn directly, not looked up by id.
    registerRailControl("help", { render: () => <button type="button">Not help</button> });

    renderWithProviders(<LeftRail active={null} onSelect={() => {}} />);
    expect(screen.getByRole("button", { name: "Toggle theme" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Not help" })).toBeInTheDocument();
  });
});
