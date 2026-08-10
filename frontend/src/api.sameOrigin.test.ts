import { afterEach, describe, expect, it, vi } from "vitest";

import { registerRequestHeaders } from "./requestHeaders";

// The build where `API_BASE` is empty — the app served from the same origin as
// the API, which is the shape `docker-compose.yml` puts behind its proxy and
// what `config.ts` falls back to when nothing is injected.
//
// It gets its own file because `vi.mock` is per file, and because this is the
// build where `req`'s guard has anything to do. With an absolute `API_BASE`
// every path lands under that origin whatever it says, so the class is closed
// by construction there and the guard is never the thing holding it. Testing
// only that build would have been testing the case that cannot fail.
vi.mock("./config", () => ({ API_BASE: "" }));

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

describe("a request on a same-origin build", () => {
  it("refuses every spelling that resolves off this origin", async () => {
    // These are not variations on a theme. The caller writes a string and the
    // URL parser reads one, and the two do not agree about what a path is: for
    // a special scheme the parser reads `\` as `/`, and it strips a raw tab, CR
    // or LF before parsing at all. So each of these resolves protocol-relative
    // — origin `elsewhere.example` — while only the first begins with `//`. A
    // guard that read the spelling let the rest through, and what went with
    // them was the contributed credential below.
    withdrawals.push(registerRequestHeaders(() => ({ "X-Model-Key": "a-secret-value" })));
    const fetchFn = mockFetch();

    for (const path of [
      "//elsewhere.example/x",
      "/\\elsewhere.example/x",
      "/\t/elsewhere.example/x",
      "/\n/elsewhere.example/x",
      "/\r/elsewhere.example/x",
      "https://elsewhere.example/x",
      "projects",
    ]) {
      await expect(api.request(path)).rejects.toThrow("rooted at the API base");
    }

    expect(fetchFn).not.toHaveBeenCalled();
  });

  it("sends a rooted path to this origin", async () => {
    // The other half of the guard: it has to let the app's own calls through,
    // and what it lets through has to be same-origin once resolved rather than
    // same-origin-looking as a string.
    const fetchFn = mockFetch();

    await api.listProjects();

    const sent = new URL(fetchFn.mock.calls[0][0]);
    expect(sent.origin).toBe(window.location.origin);
    expect(sent.pathname).toBe("/projects");
  });

  it("keeps the caller's path out of the refusal it throws", async () => {
    // `errMessage` renders `Error.message` straight into a toast, so a message
    // that interpolated the path would put whatever the caller passed on the
    // screen — and a caller that got a path wrong is the one most likely to
    // have built it out of something that should not be read aloud.
    mockFetch();

    await expect(api.request("//elsewhere.example/x?token=a-secret-value")).rejects.toThrow(
      expect.objectContaining({ message: expect.not.stringContaining("a-secret-value") }),
    );
  });
});
