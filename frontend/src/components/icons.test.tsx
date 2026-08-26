import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { CadlessIcon, ExportIcon, ImportIcon, SettingsIcon, SunIcon } from "./icons";

describe("CadlessIcon", () => {
  it("renders an inline svg with paths that inherit currentColor", () => {
    const { container } = render(<CadlessIcon />);
    const svg = container.querySelector("svg");
    expect(svg).not.toBeNull();
    // The brand mark carries the marketing site's viewBox rather than the 16×16
    // one the stroked toolbar glyphs share, because it is the same drawing and
    // rescaling the path data by hand is how the two drift apart.
    expect(svg).toHaveAttribute("viewBox", "0 0 24 24");
    expect(svg).toHaveAttribute("fill", "currentColor");
    // The solid top face plus the two shaded side faces.
    expect(container.querySelectorAll("path")).toHaveLength(3);
  });

  it("scales to the requested size for crisp 16/24/32px rendering", () => {
    for (const size of [16, 24, 32]) {
      const { container } = render(<CadlessIcon size={size} />);
      const svg = container.querySelector("svg");
      expect(svg).toHaveAttribute("width", String(size));
      expect(svg).toHaveAttribute("height", String(size));
    }
  });
});

describe("SettingsIcon", () => {
  it("is a ring with teeth, not a dot with rays", () => {
    // It used to be the second thing, which is a sun -- the same drawing as
    // `SunIcon` at a different radius, two positions away from it in the same
    // rail. The ring is what tells them apart, so the ring is what is asserted.
    const { container } = render(<SettingsIcon />);
    expect(container.querySelectorAll("circle")).toHaveLength(2);
  });

  it("would have failed on the sun that used to be here", () => {
    // Comparing the two as strings does not do it: the old glyph was a hub with
    // eight rays -- the same drawing as `SunIcon` at a different radius -- so it
    // was never byte-identical and `not.toBe` passed while the bug was there.
    // What separates a gear from a sun is the ring around the hub.
    const radii = (root: Element) =>
      [...root.querySelectorAll("circle")].map((c) => Number(c.getAttribute("r")));

    const gear = radii(render(<SettingsIcon />).container);
    const sun = radii(render(<SunIcon />).container);

    expect(sun).toHaveLength(1);
    expect(gear).toHaveLength(2);
    // And the outer circle is a ring around the hub rather than a second hub.
    expect(Math.max(...gear)).toBeGreaterThan(Math.min(...gear) * 2);
  });
});

describe("ExportIcon", () => {
  it("is the import mark with the arrow reversed", () => {
    // Drawn as a pair on purpose: same tray, opposite direction. If one is
    // redrawn without the other they stop reading as two halves of one idea.
    const exp = render(<ExportIcon />).container;
    const imp = render(<ImportIcon />).container;
    expect(exp.querySelectorAll("path")).toHaveLength(3);
    expect(imp.querySelectorAll("path")).toHaveLength(3);

    const tray = (c: Element) => c.querySelectorAll("path")[2].getAttribute("d");
    expect(tray(exp)).toBe(tray(imp));
    // ...and the stems point opposite ways.
    const stem = (c: Element) => c.querySelectorAll("path")[0].getAttribute("d");
    expect(stem(exp)).not.toBe(stem(imp));
  });
});
