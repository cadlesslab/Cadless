import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { Button, EmptyState, IconButton } from "./primitives";

describe("primitives", () => {
  it("Button applies variant + size classes and fires onClick", () => {
    const onClick = vi.fn();
    render(
      <Button variant="primary" size="sm" onClick={onClick}>
        Go
      </Button>,
    );
    const btn = screen.getByRole("button", { name: "Go" });
    expect(btn).toHaveClass("btn", "btn-primary", "btn-sm");
    fireEvent.click(btn);
    expect(onClick).toHaveBeenCalled();
  });

  it("names only what it is, with nothing left over", () => {
    // The rendered attribute is asserted whole rather than by `toHaveClass`,
    // which passes regardless of what else is in there. What is being pinned is
    // the absence: `btn-default` and `btn-md` never had a rule — they name what
    // `.btn` already is — and the trailing space came from an empty `className`
    // being interpolated anyway.
    render(<Button>Plain</Button>);
    expect(screen.getByRole("button", { name: "Plain" }).getAttribute("class")).toBe("btn");
  });

  it("keeps a caller's own class alongside its own", () => {
    // Order in the attribute is not a cascade rule — `class="mine btn"` and
    // `class="btn mine"` resolve identically, and which one wins is decided by
    // specificity and by position in the stylesheet. What is pinned here is
    // that the caller's class survives at all, and that the join is stable
    // enough to assert whole.
    render(
      <Button variant="ghost" size="sm" className="mine">
        Mine
      </Button>,
    );
    expect(screen.getByRole("button", { name: "Mine" }).getAttribute("class")).toBe(
      "btn btn-ghost btn-sm mine",
    );
  });

  it("disabled Button does not fire onClick", () => {
    const onClick = vi.fn();
    render(
      <Button disabled onClick={onClick}>
        Nope
      </Button>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Nope" }));
    expect(onClick).not.toHaveBeenCalled();
  });

  it("IconButton exposes an accessible label", () => {
    render(<IconButton label="Delete">🗑</IconButton>);
    expect(screen.getByRole("button", { name: "Delete" })).toBeInTheDocument();
  });

  it("EmptyState renders its message", () => {
    render(<EmptyState>Nothing here</EmptyState>);
    expect(screen.getByText("Nothing here")).toBeInTheDocument();
  });
});
