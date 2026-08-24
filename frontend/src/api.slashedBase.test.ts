import { afterEach, describe, expect, it, vi } from "vitest";

import { registerRequestHeaders } from "./requestHeaders";

// The same mounted-under-a-path build as `api.subPath.test.ts`, spelled with the
// trailing slash a person is at least as likely to write. It gets its own file
// because that spelling reaches different lines — the prefix is trimmed before
// anything is joined to it, and the base pathname then ends in a slash where
// the other file's does not.
// The doubled slash is here rather than a single one because a trim taking one
// slash leaves `//` as `/` — which is the case the trim exists for, arriving by
// a different road. Everything else this file asserts holds identically for the
// single-slash spelling.
vi.mock("./config", () => ({ API_BASE: "/apps/cadless/api//" }));

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

  it("joins the streams and the artifact URLs to the same base", async () => {
    // These two do not go through the guarded call, so a trim applied only
    // there would have fixed the requests and left every progress stream and
    // every download asking for `/base//...` — or, on a base of `/`, for a host
    // named after the first path segment, taking the prompt with it.
    const captured: { url: string }[] = [];
    class FakeES {
      onmessage: ((e: { data: string }) => void) | null = null;
      onerror: ((e: Event) => void) | null = null;
      close = vi.fn();
      constructor(public url: string) {
        captured.push(this);
      }
    }
    vi.stubGlobal("EventSource", FakeES as unknown as typeof EventSource);

    api.streamGenerate(2, "a rod", () => {});

    expect(new URL(captured[0].url, window.location.href).pathname).toBe(
      "/apps/cadless/api/projects/2/generate/stream",
    );
    expect(new URL(api.stepUrl(5), window.location.href).pathname).toBe(
      "/apps/cadless/api/versions/5/artifacts/step",
    );
    // The sliced job is fetched the same way and joins to the same base. Left
    // on the untrimmed one it produced a triple slash here, and on a base of
    // "/" a URL pointing at a host named `printing`.
    expect(new URL(api.gcodeUrl(5), window.location.href).pathname).toBe(
      "/apps/cadless/api/printing/versions/5/gcode",
    );
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
