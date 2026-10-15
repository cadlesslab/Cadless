import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { ViewportToolbar } from "./ViewportToolbar";
import { viewportStore } from "./viewportStore";

beforeEach(() => {
  viewportStore.setTool("none");
  // The store is one instance for the whole run, so a model with joints left
  // by one test would put the control in front of the next.
  viewportStore.setAssemblySteps(0);
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

describe("the assembly control", () => {
  const assembly = () => screen.queryByRole("group", { name: "Assembly" });
  const button = (name: string) => within(assembly()!).getByRole("button", { name });

  /** The toolbar over a model with `joints` joints, `apart` of the way through. */
  function showing(joints: number, apart = 0) {
    viewportStore.setAssemblySteps(joints);
    viewportStore.setAssemblyPosition(apart);
    render(<ViewportToolbar />);
  }

  it("is not offered for a model with nothing to play", () => {
    render(<ViewportToolbar />);
    expect(assembly()).toBeNull();
  });

  it("is offered for a model with joints, starting assembled", () => {
    showing(2);
    expect(assembly()).toBeInTheDocument();
    expect(within(assembly()!).getByText("Assembled")).toBeInTheDocument();
  });

  it("plays the parts apart", () => {
    showing(2);
    fireEvent.click(button("Take apart"));
    expect(viewportStore.get().assemblyPlay).toBe("apart");
  });

  it("plays the parts back together", () => {
    showing(2, 1);
    fireEvent.click(button("Put together"));
    expect(viewportStore.get().assemblyPlay).toBe("together");
  });

  it("steps one joint at a time, and says which joint it is on", () => {
    showing(2);
    fireEvent.click(button("Step forward"));
    expect(viewportStore.get().assemblyPosition).toBe(1);
    expect(within(assembly()!).getByText("Joint 1 of 2")).toBeInTheDocument();
    fireEvent.click(button("Step back"));
    expect(viewportStore.get().assemblyPosition).toBe(0);
    expect(within(assembly()!).getByText("Assembled")).toBeInTheDocument();
  });

  it("names the joint a part is on its way through", () => {
    showing(2, 1.5);
    expect(within(assembly()!).getByText("Joint 2 of 2")).toBeInTheDocument();
  });

  it("scrubs the parts with the slider", () => {
    showing(2);
    fireEvent.keyDown(screen.getByRole("slider", { name: "Assembly step" }), { key: "End" });
    expect(viewportStore.get().assemblyPosition).toBe(2);
  });

  it("scrubs to a point part way along a joint, not only to its ends", () => {
    showing(2);
    fireEvent.keyDown(screen.getByRole("slider", { name: "Assembly step" }), { key: "ArrowRight" });
    const { assemblyPosition } = viewportStore.get();
    expect(assemblyPosition).toBeGreaterThan(0);
    expect(assemblyPosition).toBeLessThan(1);
  });

  it("stops the play when scrubbed, so the parts stay where they were put", () => {
    showing(2);
    act(() => viewportStore.playAssembly("apart"));
    fireEvent.keyDown(screen.getByRole("slider", { name: "Assembly step" }), { key: "End" });
    expect(viewportStore.get().assemblyPlay).toBeNull();
  });

  it("offers nothing that would go further back than assembled", () => {
    showing(2);
    expect(button("Step back")).toBeDisabled();
    expect(button("Put together")).toBeDisabled();
    expect(button("Step forward")).toBeEnabled();
    expect(button("Take apart")).toBeEnabled();
  });

  it("offers nothing that would go further than every part out", () => {
    showing(2, 2);
    expect(button("Step forward")).toBeDisabled();
    expect(button("Take apart")).toBeDisabled();
    expect(button("Step back")).toBeEnabled();
    expect(button("Put together")).toBeEnabled();
  });
});
