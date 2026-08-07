import { describe, expect, it } from "vitest";

import { projectIdFromPath, projectPath } from "./routing";

describe("projectIdFromPath", () => {
  it("reads the id from a base-prefixed path", () => {
    expect(projectIdFromPath("/apps/cadless/2", "/apps/cadless/")).toBe(2);
    expect(projectIdFromPath("/apps/cadless/186", "/apps/cadless/")).toBe(186);
  });

  it("tolerates a trailing slash and extra segments", () => {
    expect(projectIdFromPath("/apps/cadless/2/", "/apps/cadless/")).toBe(2);
    expect(projectIdFromPath("/apps/cadless/2/anything", "/apps/cadless/")).toBe(2);
  });

  it("returns null for the bare base or a non-numeric segment", () => {
    expect(projectIdFromPath("/apps/cadless/", "/apps/cadless/")).toBeNull();
    expect(projectIdFromPath("/apps/cadless/foo", "/apps/cadless/")).toBeNull();
  });

  it("works at the root base (dev)", () => {
    expect(projectIdFromPath("/7", "/")).toBe(7);
    expect(projectIdFromPath("/", "/")).toBeNull();
  });
});

describe("projectPath", () => {
  it("joins the base and id", () => {
    expect(projectPath(2, "/apps/cadless/")).toBe("/apps/cadless/2");
    expect(projectPath(2, "/")).toBe("/2");
  });

  it("round-trips with projectIdFromPath", () => {
    const base = "/apps/cadless/";
    expect(projectIdFromPath(projectPath(42, base), base)).toBe(42);
  });
});
