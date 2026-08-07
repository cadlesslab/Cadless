import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ToastProvider } from "../components";
import { CodePanel } from "./CodePanel";

function renderPanel(code: string, error?: string | null) {
  return render(
    <ToastProvider>
      <CodePanel code={code} error={error} />
    </ToastProvider>,
  );
}

afterEach(() => vi.restoreAllMocks());

describe("CodePanel", () => {
  it("renders numbered, highlighted lines", () => {
    renderPanel("from build123d import *\nresult = Box(1, 1, 1)\n");
    const gutters = [...document.querySelectorAll(".code-gutter")].map((g) => g.textContent);
    expect(gutters).toEqual(["1", "2"]);
    expect(document.querySelector(".token.keyword")).not.toBeNull();
  });

  it("highlights the error line", () => {
    renderPanel("a = 1\nb = (\nc = 3\n", "syntax error (line 2)");
    const errorLine = document.querySelector(".code-line-error");
    expect(errorLine).not.toBeNull();
    expect(errorLine?.querySelector(".code-gutter")?.textContent).toBe("2");
  });

  it("copies the code to the clipboard", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    renderPanel("result = 1\n");
    fireEvent.click(screen.getByRole("button", { name: "Copy code" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("result = 1\n"));
    expect(screen.getByText("Code copied")).toBeInTheDocument();
  });

  it("collapses long scripts", () => {
    const long = Array.from({ length: 30 }, (_, i) => `line${i} = ${i}`).join("\n");
    renderPanel(long);
    const collapse = screen.getByRole("button", { name: "Collapse" });
    fireEvent.click(collapse);
    expect(screen.getByText(/more lines/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Expand" })).toBeInTheDocument();
  });
});
