import { act, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { StoreProvider, useStoreSelector } from "./state";
import { Store, initialState } from "./store";

function Counter() {
  const n = useStoreSelector((s) => s.projects.length);
  return <span data-testid="n">{n}</span>;
}

describe("useStoreSelector", () => {
  it("re-renders when the selected slice changes", () => {
    const store = new Store({ ...initialState });
    render(
      <StoreProvider store={store}>
        <Counter />
      </StoreProvider>,
    );
    expect(screen.getByTestId("n").textContent).toBe("0");

    act(() => {
      store.set({
        projects: [
          {
            id: 1,
            name: "P",
            created_at: "",
            updated_at: "",
            current_version_id: null,
          },
        ],
      });
    });
    expect(screen.getByTestId("n").textContent).toBe("1");
  });
});
