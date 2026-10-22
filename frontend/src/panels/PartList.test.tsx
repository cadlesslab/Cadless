import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { PartRef, Version } from "../api";
import { viewportStore } from "../viewport/viewportStore";
import { PartList } from "./PartList";

const NAMES = ["chassis", "shell", "wheel_fl"];

function version(): Version {
  const parts: PartRef[] = NAMES.map((name, index) => ({
    index,
    name,
    path: [],
    color: name.startsWith("wheel") ? [1, 0, 0, 1] : null,
  }));
  return {
    id: 5,
    artifacts: NAMES.map((_, part) => ({ kind: "glb", bytes: 1, part })),
    parts,
  } as Version;
}

afterEach(() => {
  viewportStore.resetParts();
  viewportStore.clearPreview();
});

describe("PartList", () => {
  it("shows every part by name, with its colour", () => {
    render(<PartList version={version()} />);
    expect(screen.getAllByRole("checkbox").map((c) => c.closest("label")!.textContent)).toEqual(NAMES);
    const swatches = screen.getAllByTestId("part-swatch");
    expect(swatches[2]).toHaveStyle({ background: "rgba(255, 0, 0, 1)" });
    expect(swatches[0]).toHaveAttribute("title", "No colour set");
  });

  it("hides a part when it is unticked, and shows it again", () => {
    render(<PartList version={version()} />);
    fireEvent.click(screen.getByRole("checkbox", { name: "shell" }));
    expect(viewportStore.get().hiddenParts).toEqual([1]);
    expect(screen.getByRole("checkbox", { name: "shell" })).not.toBeChecked();

    fireEvent.click(screen.getByRole("button", { name: "Show all" }));
    expect(viewportStore.get().hiddenParts).toEqual([]);
  });

  it("picks out the part pointed at, and lets go of it", () => {
    render(<PartList version={version()} />);
    const row = screen.getByRole("checkbox", { name: "wheel_fl" }).closest("li")!;
    fireEvent.mouseEnter(row);
    expect(viewportStore.get().highlightedPart).toBe(2);
    fireEvent.mouseLeave(row);
    expect(viewportStore.get().highlightedPart).toBeNull();
  });

  it("lets go of the part picked out when it goes away", () => {
    const { unmount } = render(<PartList version={version()} />);
    fireEvent.mouseEnter(screen.getByRole("checkbox", { name: "shell" }).closest("li")!);
    unmount();
    expect(viewportStore.get().highlightedPart).toBeNull();
  });

  it("offers nothing to change while something else is on screen", () => {
    viewportStore.showPreview({ url: "/depot/a", title: "Bracket" });
    render(<PartList version={version()} />);
    expect(screen.getByRole("checkbox", { name: "shell" })).toBeDisabled();
  });

  it("offers nothing to change while a catalogue says it has no mesh", () => {
    viewportStore.showPreview({ url: null, title: "Bracket", note: "no mesh" });
    render(<PartList version={version()} />);
    expect(screen.getByRole("checkbox", { name: "shell" })).toBeDisabled();
  });

  it("shows every part again when it goes away", () => {
    const { unmount } = render(<PartList version={version()} />);
    fireEvent.click(screen.getByRole("checkbox", { name: "shell" }));
    unmount();
    expect(viewportStore.get().hiddenParts).toEqual([]);
  });

  it("picks out nothing while something else is on screen", () => {
    viewportStore.showPreview({ url: "/depot/a", title: "Bracket" });
    render(<PartList version={version()} />);
    fireEvent.mouseEnter(screen.getByRole("checkbox", { name: "shell" }).closest("li")!);
    expect(viewportStore.get().highlightedPart).toBeNull();
  });

  it("lists nothing for a model printed whole", () => {
    const whole = { ...version(), artifacts: [{ kind: "glb", bytes: 1, part: 0 }] } as Version;
    const { container } = render(<PartList version={whole} />);
    expect(container).toBeEmptyDOMElement();
  });
});
