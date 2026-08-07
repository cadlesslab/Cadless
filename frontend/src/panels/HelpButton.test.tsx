import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { HelpButton } from "./HelpButton";

describe("HelpButton", () => {
  it("opens the shortcuts panel via the button", () => {
    render(<HelpButton />);
    expect(screen.queryByText("Keyboard shortcuts")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Keyboard shortcuts" }));
    expect(screen.getByText("Keyboard shortcuts")).toBeInTheDocument();
    expect(screen.getByText(/Generate \/ Refine/)).toBeInTheDocument();
  });

  it("opens via the ? key", () => {
    render(<HelpButton />);
    fireEvent.keyDown(window, { key: "?" });
    expect(screen.getByText("Keyboard shortcuts")).toBeInTheDocument();
  });
});
