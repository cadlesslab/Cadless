/** Theme persistence + application. */
export type Theme = "light" | "dark";

const KEY = "cadless-theme";

export function getStoredTheme(): Theme {
  const stored =
    typeof localStorage !== "undefined" ? localStorage.getItem(KEY) : null;
  return stored === "light" || stored === "dark" ? stored : "dark";
}

export function applyTheme(theme: Theme): void {
  if (typeof document !== "undefined") {
    document.documentElement.dataset.theme = theme;
  }
  try {
    localStorage.setItem(KEY, theme);
  } catch {
    /* storage may be unavailable (private mode / tests) */
  }
}
