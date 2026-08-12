/** The rail's bottom seam.
 *
 * The case that matters most is the empty one: every build that registers
 * nothing — which is every local build — must draw exactly the rail it drew
 * before this registry existed. A seam is supposed to be invisible until
 * something fills it. */
import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it } from "vitest";

import { LeftRail } from "./LeftRail";
import {
  registerRailControl,
  registeredRailControls,
  unregisterRailControl,
} from "./railControls";
import { renderWithProviders } from "../test/utils";

/** The accessible names of the two controls the app owns, named once so a test
 *  cannot quietly stop checking one of them. */
const HELP = "Keyboard shortcuts";
const THEME = "Toggle theme";

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
    // Both of the app's own, by name. Naming only one leaves the other free to
    // disappear with the suite green, which is what happened to the first
    // version of this file.
    expect(screen.getByRole("button", { name: HELP })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: THEME })).toBeInTheDocument();
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
    //
    // Compared by document position rather than by index in `children`, because
    // `findIndex` answers -1 for "not a direct child" and -1 is less than every
    // index — so a refactor that nested the controls one level deeper would
    // have satisfied the assertion while breaking exactly what it pins.
    const account = bottom?.querySelector(".rail-control");
    const theme = screen.getByRole("button", { name: THEME });
    expect(account).not.toBeNull();
    expect(
      account && account.compareDocumentPosition(theme) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("keeps the rail's own controls out of the registry's reach", () => {
    // A build cannot replace help or the theme by registering over them: they
    // are drawn directly, not looked up by id.
    registerRailControl("help", { render: () => <button type="button">Not help</button> });

    renderWithProviders(<LeftRail active={null} onSelect={() => {}} />);
    expect(screen.getByRole("button", { name: HELP })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: THEME })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Not help" })).toBeInTheDocument();
  });

  it("gives each control a hook scope of its own", () => {
    // The control is mounted, not called. Called inline its hooks would belong
    // to the rail, and registering a second control mid-life would change how
    // many the rail ran — "rendered more hooks than during the previous render".
    function Counting() {
      const [n] = useState(() => 1);
      return <button type="button">Counted {n}</button>;
    }
    registerRailControl("counting", { render: () => <Counting /> });
    registerRailControl("plain", { render: () => <button type="button">Plain</button> });

    renderWithProviders(<LeftRail active={null} onSelect={() => {}} />);
    // Pressing the theme toggle re-renders the rail, which is when an inlined
    // hook order would go wrong.
    fireEvent.click(screen.getByRole("button", { name: THEME }));
    expect(screen.getByRole("button", { name: "Counted 1" })).toBeInTheDocument();
  });

  it("hands back a withdrawal that only removes its own registration", () => {
    const remove = registerRailControl("account", { render: () => null });
    // A later registration under the same id belongs to whoever made it; a
    // stale handle must not take it away.
    registerRailControl("account", { render: () => null });
    remove();
    expect(registeredRailControls().map(({ id }) => id)).toEqual(["account"]);
  });
});
