import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { ViewportToolbar } from "./ViewportToolbar";
import { viewportStore } from "./viewportStore";

beforeEach(() => {
  viewportStore.setTool("none");
});

describe("ViewportToolbar (slim overlay)", () => {
  it("selects inspection tools and reveals section/measure controls", () => {
    render(<ViewportToolbar />);
    // section: axis + offset controls appear
    fireEvent.click(screen.getByRole("radio", { name: "Section" }));
    expect(viewportStore.get().tool).toBe("section");
    expect(screen.getByRole("radio", { name: "Y" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("radio", { name: "Y" }));
    expect(viewportStore.get().sectionAxis).toBe("y");

    // measure: a Clear control appears
    fireEvent.click(screen.getByRole("radio", { name: "Measure" }));
    expect(viewportStore.get().tool).toBe("measure");
    viewportStore.addMeasurePoint([0, 0, 0]);
    fireEvent.click(screen.getByRole("button", { name: "Clear" }));
    expect(viewportStore.get().measurePoints).toEqual([]);

    fireEvent.click(screen.getByRole("radio", { name: "Orbit" }));
    expect(viewportStore.get().tool).toBe("none");
  });

  it("renders the opacity control", () => {
    render(<ViewportToolbar />);
    expect(screen.getByRole("slider", { name: "Opacity" })).toBeInTheDocument();
  });
});
