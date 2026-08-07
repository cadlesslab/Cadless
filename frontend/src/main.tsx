import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import { ToastProvider } from "./components";
import { StoreProvider } from "./state";
import { Store } from "./store";
import { applyTheme, getStoredTheme } from "./theme/theme";

import "./theme/tokens.css";
import "./components/components.css";
import "./styles/app.css";

applyTheme(getStoredTheme()); // avoid a flash of the wrong theme before React mounts

const store = new Store();

createRoot(document.getElementById("app")!).render(
  <StrictMode>
    <StoreProvider store={store}>
      <ToastProvider>
        <App />
      </ToastProvider>
    </StoreProvider>
  </StrictMode>,
);
