import { render } from "@testing-library/react";
import type { ReactNode } from "react";

import { ToastProvider } from "../components";
import { StoreProvider } from "../state";
import { type AppState, Store, initialState } from "../store";

/** Render a component tree wrapped in the store + toast providers. */
export function renderWithProviders(ui: ReactNode, partial?: Partial<AppState>) {
  const store = new Store({ ...initialState, ...partial });
  const utils = render(
    <StoreProvider store={store}>
      <ToastProvider>{ui}</ToastProvider>
    </StoreProvider>,
  );
  return { store, ...utils };
}
