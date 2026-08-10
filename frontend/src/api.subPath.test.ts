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

    // The backslash one climbs with separators the parser folds to `/` before
    // resolving — the same disagreement between spelling and parsing that the
    // origin case turns on, arriving here as a path traversal.
    //
    // The last two are the ones a prefix comparison lets through: they do not
    // climb above the base's parent, they land on a *sibling* whose name merely
    // begins with the base's. On a shared host that is somebody else's app.
    for (const path of [
      "/../../admin",
      "/../other/x",
      "/..\\..\\admin",
      "/../api-admin/x",
      "/../apifoo/x",
    ]) {
      await expect(api.request(path)).rejects.toThrow("rooted at the API base");
    }

    expect(fetchFn).not.toHaveBeenCalled();
  });

  it("refuses a path that climbs out once a proxy decodes it", async () => {
    // The URL parser does not decode an escaped separator, so `..%2f..%2fadmin`
    // stays a single segment here and the containment check says it is inside.
    // A proxy that normalises before it matches — nginx does; the Caddy this
    // tree bundles does not — sees `/apps/cadless/admin` instead: outside the
    // base, with the credential attached, and on a shared host that is somebody
    // else's app.
    withdrawals.push(registerRequestHeaders(() => ({ "X-Model-Key": "a-secret-value" })));
    const fetchFn = mockFetch();

    for (const path of ["/..%2f..%2fadmin", "/catalog/..%2F..%2Fadmin", "/..%5c..%5cadmin"]) {
      await expect(api.request(path)).rejects.toThrow("rooted at the API base");
    }

    expect(fetchFn).not.toHaveBeenCalled();
  });

  it("refuses a climb hidden behind a decoded query or fragment mark", async () => {
    // Decoding is what exposes the traversal, and it exposes a `?` or a `#` the
    // same way. Read back by a parser those end the path and take the rest of
    // it — the traversal included — out of the ruling, so what is left looks
    // like an ordinary segment. Escaped again first, the whole path stays a
    // path and the climb is seen.
    withdrawals.push(registerRequestHeaders(() => ({ "X-Model-Key": "a-secret-value" })));
    const fetchFn = mockFetch();

    for (const path of [
      "/%23/..%2f..%2fadmin",
      "/%3f/..%2f..%2fadmin",
      "/x%23%2f..%2f..%2fadmin",
    ]) {
      await expect(api.request(path)).rejects.toThrow("rooted at the API base");
    }

    expect(fetchFn).not.toHaveBeenCalled();
  });

  it("still sends an escaped separator that stays inside the base", async () => {
    // The other half of the same rule, and the reason it cannot simply refuse
    // `%2F`: a catalog origin key is `encodeURIComponent`d into one segment, so
    // an escaped slash inside a legitimate route is ordinary rather than a
    // signal. Decoded it is still under the base, so it goes — and it goes
    // exactly as spelled, because what was decoded was only ever the ruling.
    const fetchFn = mockFetch();

    await api.request("/catalog/origins/a%2Fb");

    expect(new URL(fetchFn.mock.calls[0][0]).pathname).toBe(
      "/apps/cadless/api/catalog/origins/a%2Fb",
    );
  });

  it("sends a path that resolves onto the base itself", async () => {
    // The base is inside the base. `/../api` climbs one level and comes back to
    // exactly `/apps/cadless/api` — no trailing slash, so a containment test
    // written only as "starts with the base plus a slash" refuses it, and a
    // build is entitled to a route at its own root.
    const fetchFn = mockFetch();

    await api.request("/../api");

    expect(new URL(fetchFn.mock.calls[0][0]).pathname).toBe("/apps/cadless/api");
  });

  it("refuses a percent escape no decoder will accept", async () => {
    const fetchFn = mockFetch();

    for (const path of ["/%zz/x", "/%/x"]) {
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
