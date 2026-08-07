import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { TagPicker } from "./TagPicker";

const AVAILABLE = ["engine", "piston", "connecting rod", "86 mm bore", "M4"];

function picker(props: Partial<Parameters<typeof TagPicker>[0]> = {}) {
  return (
    <TagPicker available={AVAILABLE} selected={[]} onChange={() => {}} {...props} />
  );
}

describe("TagPicker", () => {
  it("suggests nothing until something is typed", () => {
    // A hundred tags dropped on the panel unprompted is the clutter this
    // replaces, not a feature.
    render(picker());

    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("narrows the suggestions as it is typed into", () => {
    render(picker());

    fireEvent.change(screen.getByLabelText("Tag"), { target: { value: "on" } });

    const shown = screen.getAllByRole("option").map((o) => o.textContent);
    expect(shown).toEqual(["piston", "connecting rod"]);
  });

  it("matches without regard to case, because the server does not", () => {
    // The tag itself is compared exactly over there, which is exactly why the
    // typing here has to be forgiving: what is picked is always a real tag.
    render(picker());

    fireEvent.change(screen.getByLabelText("Tag"), { target: { value: "m4" } });

    expect(screen.getAllByRole("option").map((o) => o.textContent)).toEqual(["M4"]);
  });

  it("hands back the tag as it is spelled, not as it was typed", () => {
    const onChange = vi.fn();
    render(picker({ onChange }));

    fireEvent.change(screen.getByLabelText("Tag"), { target: { value: "m4" } });
    fireEvent.click(screen.getByRole("option", { name: "M4" }));

    expect(onChange).toHaveBeenCalledWith(["M4"]);
  });

  it("clears what was typed once a tag is chosen", () => {
    render(picker());
    fireEvent.change(screen.getByLabelText("Tag"), { target: { value: "eng" } });
    fireEvent.click(screen.getByRole("option", { name: "engine" }));

    expect(screen.getByLabelText("Tag")).toHaveValue("");
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("does not offer a tag that is already on", () => {
    render(picker({ selected: ["engine"] }));

    fireEvent.change(screen.getByLabelText("Tag"), { target: { value: "en" } });

    expect(screen.queryByRole("option", { name: "engine" })).toBeNull();
  });

  it("shows what is on, and takes one off again", () => {
    const onChange = vi.fn();
    render(picker({ selected: ["engine", "M4"], onChange }));

    fireEvent.click(screen.getByRole("button", { name: "Remove tag engine" }));

    expect(onChange).toHaveBeenCalledWith(["M4"]);
  });

  it("says when nothing on screen carries what was typed", () => {
    // Silence would read as "still loading". The tags offered are the ones the
    // arrived listings carry, so a miss is worth saying out loud.
    render(picker());

    fireEvent.change(screen.getByLabelText("Tag"), { target: { value: "zzz" } });

    expect(screen.queryByRole("option")).toBeNull();
    expect(screen.getByText(/No tag here matches/)).toBeInTheDocument();
  });

  it("takes the first suggestion on Enter, so the keyboard alone can do it", () => {
    const onChange = vi.fn();
    render(picker({ onChange }));

    const input = screen.getByLabelText("Tag");
    fireEvent.change(input, { target: { value: "on" } });
    fireEvent.keyDown(input, { key: "Enter" });

    expect(onChange).toHaveBeenCalledWith(["piston"]);
  });

  it("refuses to add a tag on Enter when nothing matches", () => {
    // Typing an exact tag by hand is the trap this control exists to remove:
    // the server compares exactly, so a near miss finds nothing and looks like
    // an empty result.
    const onChange = vi.fn();
    render(picker({ onChange }));

    const input = screen.getByLabelText("Tag");
    fireEvent.change(input, { target: { value: "zzz" } });
    fireEvent.keyDown(input, { key: "Enter" });

    expect(onChange).not.toHaveBeenCalled();
  });
});
