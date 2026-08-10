import { afterEach, describe, expect, it, vi } from "vitest";

import { registerRequestHeaders } from "./requestHeaders";

// The build where `API_BASE` is a path on this origin rather than a whole URL —
// the app and the API served together under a prefix. It is the shape a host
// that mounts this engine beside other things produces, and it is the only one
// where a path can stay on this origin and still leave the API base.
//
// Its own file for the same reason as `api.sameOrigin.test.ts`: `vi.mock` is
// settled per file, so a build shape is.
vi.mock("./config", () => ({ API_BASE: "/apps/cadless/api" }));

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

describe("a request on a build mounted under a path", () => {
  it("refuses a path that climbs out of the API base", async () => {
    // Same origin, so the origin comparison says nothing — and `..` is resolved
    // by the parser rather than sent, so the request would arrive at a route
    // this base does not own, carrying the credential below to whatever else
    // the host serves there.
    withdrawals.push(registerRequestHeaders(() => ({ "X-Model-Key": "a-secret-value" })));
    const fetchFn = mockFetch();

    // The last one climbs with backslashes, which the parser folds to `/`
    // before resolving — the same disagreement between spelling and parsing
    // that the origin case turns on, arriving here as a path traversal.
    for (const path of ["/../../admin", "/../other/x", "/..\\..\\admin"]) {
      await expect(api.request(path)).rejects.toThrow("rooted at the API base");
    }

    expect(fetchFn).not.toHaveBeenCalled();
  });

  it("sends a rooted path under the API base", async () => {
    const fetchFn = mockFetch();

    await api.listProjects();

    const sent = new URL(fetchFn.mock.calls[0][0]);
    expect(sent.origin).toBe(window.location.origin);
    expect(sent.pathname).toBe("/apps/cadless/api/projects");
  });
});
