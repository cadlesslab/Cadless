import { afterEach, describe, expect, it, vi } from "vitest";

import { registerRequestHeaders } from "./requestHeaders";

// The same mounted-under-a-path build as `api.subPath.test.ts`, spelled with the
// trailing slash a person is at least as likely to write. It gets its own file
// because that spelling reaches different lines — the prefix is trimmed before
// anything is joined to it, and the base pathname then ends in a slash where
// the other file's does not.
vi.mock("./config", () => ({ API_BASE: "/apps/cadless/api/" }));

const api = await import("./api");

function mockFetch() {
  const fn = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    statusText: "x",
    json: async () => [],
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

const withdrawals: (() => void)[] = [];

afterEach(() => {
  while (withdrawals.length) withdrawals.pop()?.();
  vi.unstubAllGlobals();
});

describe("a request on a build whose base ends in a slash", () => {
  it("joins the path without doubling the separator", async () => {
    // Left alone, `/apps/cadless/api/` + `/projects` is `/apps/cadless/api//projects`
    // — a real path on most servers and not the one anybody meant.
    const fetchFn = mockFetch();

    await api.listProjects();

    expect(new URL(fetchFn.mock.calls[0][0]).pathname).toBe("/apps/cadless/api/projects");
  });

  it("sends a path that lands on the base itself", async () => {
    // The base is inside the base. Comparing only "starts with the base plus a
    // slash" would refuse this, and a build is entitled to a route at its root.
    const fetchFn = mockFetch();

    await api.request("/");

    expect(new URL(fetchFn.mock.calls[0][0]).pathname).toBe("/apps/cadless/api/");
  });

  it("still refuses a path that climbs out", async () => {
    withdrawals.push(registerRequestHeaders(() => ({ "X-Model-Key": "a-secret-value" })));
    const fetchFn = mockFetch();

    for (const path of ["/../../admin", "/../api-admin/x", "/..%2f..%2fadmin"]) {
      await expect(api.request(path)).rejects.toThrow("rooted at the API base");
    }

    expect(fetchFn).not.toHaveBeenCalled();
  });
});
