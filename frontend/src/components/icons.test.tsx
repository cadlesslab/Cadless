import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { CadlessIcon } from "./icons";

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
