import { afterEach, describe, expect, it } from "vitest";

import { _reload, getNote, setNote } from "./annotations";

afterEach(() => {
  localStorage.clear();
  _reload();
});

describe("annotations", () => {
  it("stores and reads a note, persisting to localStorage", () => {
    setNote(7, "Mounting plate");
    expect(getNote(7)).toBe("Mounting plate");
    expect(localStorage.getItem("cadless-version-notes")).toContain("Mounting plate");
  });

  it("clears a note when set to blank", () => {
    setNote(7, "x");
    setNote(7, "   ");
    expect(getNote(7)).toBeUndefined();
  });

  it("reloads from storage", () => {
    localStorage.setItem("cadless-version-notes", JSON.stringify({ "9": "Flange" }));
    _reload();
    expect(getNote(9)).toBe("Flange");
  });
});
