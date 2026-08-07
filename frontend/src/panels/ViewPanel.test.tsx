import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { viewportStore } from "../viewport/viewportStore";
import { ViewPanel } from "./ViewPanel";

beforeEach(() => {
  viewportStore.setProjection("perspective");
  viewportStore.setDisplayMode("shaded");
  viewportStore.setGridVisible(true);
  viewportStore.setAxesVisible(true);
});

describe("ViewPanel", () => {
  it("dispatches named-view, fit and reset commands", () => {
    render(<ViewPanel />);
    fireEvent.click(screen.getByRole("button", { name: "Top" }));
    expect(viewportStore.get().command).toMatchObject({ kind: "view", view: "top" });
    fireEvent.click(screen.getByRole("button", { name: "Zoom to fit" }));
    expect(viewportStore.get().command?.kind).toBe("fit");
    fireEvent.click(screen.getByRole("button", { name: "Reset" }));
    expect(viewportStore.get().command?.kind).toBe("reset");
  });

  it("switches projection and display mode", () => {
    render(<ViewPanel />);
    fireEvent.click(screen.getByRole("radio", { name: "Ortho" }));
    expect(viewportStore.get().projection).toBe("orthographic");
    fireEvent.click(screen.getByRole("radio", { name: "Wire" }));
    expect(viewportStore.get().displayMode).toBe("wireframe");
  });

  it("toggles grid and axes", () => {
    render(<ViewPanel />);
    const grid = screen.getByRole("button", { name: "Grid" });
    expect(grid).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(grid);
    expect(viewportStore.get().gridVisible).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Axes" }));
    expect(viewportStore.get().axesVisible).toBe(false);
  });
});
