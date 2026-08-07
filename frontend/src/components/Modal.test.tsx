import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ConfirmDialog, PromptDialog } from "./Modal";

describe("PromptDialog", () => {
  it("submits the trimmed value and disables submit when empty", () => {
    const onSubmit = vi.fn();
    render(
      <PromptDialog
        open
        title="New project"
        label="Project name"
        confirmLabel="Create"
        onSubmit={onSubmit}
        onClose={() => {}}
      />,
    );
    const create = screen.getByRole("button", { name: "Create" });
    expect(create).toBeDisabled();

    fireEvent.change(screen.getByLabelText("Project name"), { target: { value: "  Bracket  " } });
    expect(create).toBeEnabled();
    fireEvent.click(create);
    expect(onSubmit).toHaveBeenCalledWith("Bracket");
  });

  it("renders nothing when closed", () => {
    render(
      <PromptDialog open={false} title="x" label="y" onSubmit={() => {}} onClose={() => {}} />,
    );
    expect(screen.queryByLabelText("y")).not.toBeInTheDocument();
  });
});

describe("ConfirmDialog", () => {
  it("fires onConfirm when the action is clicked", () => {
    const onConfirm = vi.fn();
    render(
      <ConfirmDialog
        open
        title="Delete project?"
        message="gone forever"
        onConfirm={onConfirm}
        onClose={() => {}}
      />,
    );
    expect(screen.getByText("gone forever")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(onConfirm).toHaveBeenCalled();
  });
});
