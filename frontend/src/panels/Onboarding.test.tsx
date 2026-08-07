import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { Onboarding } from "./Onboarding";

afterEach(() => localStorage.clear());

describe("Onboarding", () => {
  it("shows the first-run tip and hides + remembers on dismiss", () => {
    const { rerender } = render(<Onboarding />);
    expect(screen.getByRole("note")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Dismiss onboarding" }));
    expect(screen.queryByRole("note")).not.toBeInTheDocument();
    expect(localStorage.getItem("cadless-onboarded")).toBe("1");

    // a fresh mount stays dismissed
    rerender(<Onboarding />);
    expect(screen.queryByRole("note")).not.toBeInTheDocument();
  });

  it("does not render when already onboarded", () => {
    localStorage.setItem("cadless-onboarded", "1");
    render(<Onboarding />);
    expect(screen.queryByRole("note")).not.toBeInTheDocument();
  });
});
