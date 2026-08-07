/** The project actions a panel shipped outside this tree is given.
 *
 * `plugin.ts` withholds `useApp`, so these two are the only way a panel reaches
 * this machine's projects. Both properties matter to a panel author and neither
 * is visible from the type alone: that the surface is exactly two actions, and
 * that they fail by raising a toast rather than by rejecting. A panel written
 * against a rejecting promise would put its error handling in a `catch` that is
 * never entered, and would look correct while saying nothing. */
import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "./test/utils";
import { useProjectActions } from "./useApp";

vi.mock("./actions", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./actions")>()),
  selectProject: vi.fn(() => Promise.reject(new Error("boom"))),
}));

function Probe({ onKeys }: { onKeys?: (keys: string[]) => void }) {
  const actions = useProjectActions();
  onKeys?.(Object.keys(actions));
  return <button onClick={() => void actions.selectProject(1)}>open</button>;
}

describe("useProjectActions", () => {
  it("hands a panel two actions and nothing else", () => {
    let keys: string[] = [];
    renderWithProviders(<Probe onKeys={(k) => (keys = k)} />);

    expect([...keys].sort()).toEqual(["cloneCatalogItem", "selectProject"]);
  });

  it("carries the app's toast-on-error guard, so a failing action never rejects", async () => {
    renderWithProviders(<Probe />);

    fireEvent.click(screen.getByRole("button", { name: "open" }));

    expect(await screen.findByText("Action failed")).toBeInTheDocument();
    expect(await screen.findByText("boom")).toBeInTheDocument();
  });
});
