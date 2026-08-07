import { afterEach, describe, expect, it } from "vitest";

import { applyTheme, getStoredTheme } from "./theme";

afterEach(() => {
  localStorage.clear();
  delete document.documentElement.dataset.theme;
});

describe("theme", () => {
  it("defaults to dark when nothing stored", () => {
    expect(getStoredTheme()).toBe("dark");
  });

  it("applyTheme sets the data attribute and persists", () => {
    applyTheme("light");
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(getStoredTheme()).toBe("light");
  });

  it("ignores invalid stored values", () => {
    localStorage.setItem("cadless-theme", "purple");
    expect(getStoredTheme()).toBe("dark");
  });
});
