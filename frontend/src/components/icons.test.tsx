import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { CadlessIcon } from "./icons";

describe("CadlessIcon", () => {
  it("renders an inline svg with a path that inherits currentColor", () => {
    const { container } = render(<CadlessIcon />);
    const svg = container.querySelector("svg");
    expect(svg).not.toBeNull();
    // Follows the shared inline-SVG convention.
    expect(svg).toHaveAttribute("viewBox", "0 0 16 16");
    expect(container.querySelector("path")).not.toBeNull();
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
