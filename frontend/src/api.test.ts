import { afterEach, describe, expect, it, vi } from "vitest";

import * as api from "./api";
import { registerRequestHeaders } from "./requestHeaders";

function mockFetch(status: number, body: unknown) {
  const fn = vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    statusText: "x",
    json: async () => body,
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

afterEach(() => vi.unstubAllGlobals());

describe("REST client", () => {
  it("listProjects GETs /projects and returns parsed JSON", async () => {
    const fetchFn = mockFetch(200, [{ id: 1, name: "P" }]);
    const out = await api.listProjects();
    expect(out[0].name).toBe("P");
    expect(fetchFn.mock.calls[0][0]).toMatch(/\/projects$/);
  });

  it("importCatalog sends the file as a package rather than as a form", async () => {
    // The server refuses every content type a form could have sent, which is
    // what makes a browser ask permission before another site posts one. This
    // header looks removable — `req` supplies a default — and dropping it would
    // have the app's own import refused with a 415.
    const fetchFn = mockFetch(200, { id: "l-bracket" });
    const file = new File([new Uint8Array([80, 75, 3, 4])], "l-bracket.cls");

    await api.importCatalog(file, "a".repeat(64));

    const [url, init] = fetchFn.mock.calls[0];
    expect(url).toMatch(/\/packages\/import\?/);
    expect(url).toContain("filename=l-bracket.cls");
    expect(url).toContain(`expected_digest=${"a".repeat(64)}`);
    expect(init.method).toBe("POST");
    // Read off `Headers` rather than off a key: names are case-insensitive, and
    // asking for one spelling of an object would pass while the other spelling
    // was also on the wire.
    expect(init.headers.get("content-type")).toBe("application/octet-stream");
    expect(init.body).toBe(file);
  });

  it("fetchHeldOrigins asks for every item held from one origin at once", async () => {
    // Its own route, and no paging: a panel marks a whole page of search results
    // against this, and a window over the catalog would silently mark only the
    // first hundred.
    const fetchFn = mockFetch(200, { items: [{ house_id: "l-bracket", catalog_id: "cat-1" }] });

    const out = await api.fetchHeldOrigins("depot");

    expect(out.items[0].catalog_id).toBe("cat-1");
    expect(fetchFn.mock.calls[0][0]).toMatch(/\/catalog\/origins\/depot$/);
  });

  it("fetchHeldOrigins escapes the origin it was given", async () => {
    // The kind reaches a path segment. It comes from a build's own registration
    // rather than from a user, but a key with a slash in it would otherwise
    // address a different route entirely and be answered as one.
    const fetchFn = mockFetch(200, { items: [] });

    await api.fetchHeldOrigins("a/b");

    expect(fetchFn.mock.calls[0][0]).toMatch(/\/catalog\/origins\/a%2Fb$/);
  });

  it("fetchCatalogOrigins reads the labels from the registry that decides them", async () => {
    const fetchFn = mockFetch(200, { origins: [{ key: "local", label: "Local" }] });

    const out = await api.fetchCatalogOrigins();

    expect(out.origins[0].label).toBe("Local");
    expect(fetchFn.mock.calls[0][0]).toMatch(/\/catalog\/origins$/);
  });

  it("fetchCatalog carries the origin filter through to the query", async () => {
    const fetchFn = mockFetch(200, { items: [], sources: [] });

    await api.fetchCatalog({ source: "depot", limit: 24 });

    expect(fetchFn.mock.calls[0][0]).toContain("source=depot");
  });

  it("createProject POSTs the name", async () => {
    const fetchFn = mockFetch(201, { id: 2, name: "New" });
    await api.createProject("New");
    const [, init] = fetchFn.mock.calls[0];
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ name: "New" });
  });

  it("branchFromVersion POSTs the source version to /projects/{id}/branch", async () => {
    const fetchFn = mockFetch(201, {
      id: 9, name: "Origin (branch)", current_version_id: 5, branched_from_version_id: 4,
    });
    const out = await api.branchFromVersion(2, 4);
    const [url, init] = fetchFn.mock.calls[0];
    expect(url).toMatch(/\/projects\/2\/branch$/);
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ version_id: 4 });
    expect(out.id).toBe(9);
    expect(out.branched_from_version_id).toBe(4);
  });

  it("generate POSTs the prompt to the project", async () => {
    const fetchFn = mockFetch(200, { ok: true, attempt_count: 1, version: {} });
    await api.generate(7, "a cube");
    const [url, init] = fetchFn.mock.calls[0];
    expect(url).toMatch(/\/projects\/7\/generate$/);
    expect(JSON.parse(init.body)).toEqual({ prompt: "a cube" });
  });

  it("deleteProject handles 204 (no body)", async () => {
    mockFetch(204, undefined);
    await expect(api.deleteProject(3)).resolves.toBeUndefined();
  });

  it("throws ApiError with detail on non-2xx", async () => {
    mockFetch(404, { detail: "project not found" });
    await expect(api.getProject(99)).rejects.toMatchObject({
      name: "ApiError",
      status: 404,
      message: "project not found",
    });
  });

  it("refine POSTs prior_version_id + delta_prompt", async () => {
    const fetchFn = mockFetch(200, { ok: true, attempt_count: 1, version: {} });
    await api.refine(7, 42, "make the hole 8 mm");
    const [url, init] = fetchFn.mock.calls[0];
    expect(url).toMatch(/\/projects\/7\/generate$/);
    expect(JSON.parse(init.body)).toEqual({
      prior_version_id: 42,
      delta_prompt: "make the hole 8 mm",
    });
  });

  it("reparametrize POSTs param overrides", async () => {
    const fetchFn = mockFetch(200, { ok: true, error: null, version: {} });
    await api.reparametrize(42, { hole_dia: 8 });
    const [url, init] = fetchFn.mock.calls[0];
    expect(url).toMatch(/\/versions\/42\/reparametrize$/);
    expect(JSON.parse(init.body)).toEqual({ params: { hole_dia: 8 } });
  });

  it("builds artifact URLs for every format", () => {
    expect(api.stepUrl(5)).toMatch(/\/versions\/5\/artifacts\/step$/);
    expect(api.glbUrl(5)).toMatch(/\/versions\/5\/artifacts\/glb$/);
    expect(api.artifactUrl(5, "stl")).toMatch(/\/versions\/5\/artifacts\/stl$/);
    expect(api.artifactUrl(5, "obj")).toMatch(/\/versions\/5\/artifacts\/obj$/);
  });
});

describe("SSE client", () => {
  it("parses events, opens the right URL, and closes on done", () => {
    const events: api.ProgressEvent[] = [];
    const closed = vi.fn();
    const captured: FakeES[] = [];

    class FakeES {
      onmessage: ((e: { data: string }) => void) | null = null;
      onerror: ((e: Event) => void) | null = null;
      close = closed;
      constructor(public url: string) {
        captured.push(this);
      }
    }
    vi.stubGlobal("EventSource", FakeES as unknown as typeof EventSource);

    api.streamGenerate(2, "a rod", (e) => events.push(e));
    const stream = captured[0];
    expect(stream.url).toContain("/projects/2/generate/stream?prompt=a%20rod");

    stream.onmessage?.({ data: JSON.stringify({ event: "start", intent: "rod", max_tries: 3 }) });
    stream.onmessage?.({
      data: JSON.stringify({ event: "done", version_id: 9, ok: true, attempt_count: 1 }),
    });

    expect(events.some((e) => e.event === "start")).toBe(true);
    expect(events.some((e) => e.event === "done")).toBe(true);
    expect(closed).toHaveBeenCalled();
  });

  it("streamRefine opens the refine URL with prior_version_id + delta_prompt", () => {
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

    api.streamRefine(2, 9, "make it bigger", () => {});
    expect(captured[0].url).toContain(
      "/projects/2/generate/stream?prior_version_id=9&delta_prompt=make%20it%20bigger",
    );
  });
});

/** Build a Response whose body streams the given SSE text lines. */
function sseResponse(chunks: string[]): Response {
  const encoder = new TextEncoder();
  let i = 0;
  const body = {
    getReader() {
      return {
        read: async () =>
          i < chunks.length
            ? { done: false, value: encoder.encode(chunks[i++]) }
            : { done: true, value: undefined },
        releaseLock() {},
        cancel() {},
      };
    },
  };
  return { ok: true, status: 200, body } as unknown as Response;
}

describe("chat SSE client", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("POSTs the message and parses turn events", async () => {
    const fetchFn = vi.fn().mockResolvedValue(
      sseResponse([
        'data: {"event":"turn_start"}\n\n',
        'data: {"event":"text_delta","text":"hi"}\n\n',
        'data: {"event":"turn_end","stop_reason":"end_turn"}\n\n',
      ]),
    );
    vi.stubGlobal("fetch", fetchFn);

    const seen: api.ChatEvent[] = [];
    await api.streamChat(7, "a cube", (e) => seen.push(e));

    const [url, init] = fetchFn.mock.calls[0];
    expect(url).toMatch(/\/projects\/7\/chat$/);
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ message: "a cube", forge: false });
    expect(seen.map((e) => e.event)).toEqual(["turn_start", "text_delta", "turn_end"]);
  });

  it("sends forge:true when the turn opts into forge mode", async () => {
    const fetchFn = vi.fn().mockResolvedValue(
      sseResponse(['data: {"event":"turn_end","stop_reason":"end_turn"}\n\n']),
    );
    vi.stubGlobal("fetch", fetchFn);

    await api.streamChat(7, "a cube", () => {}, undefined, true);

    const init = fetchFn.mock.calls[0][1];
    expect(JSON.parse(init.body)).toEqual({ message: "a cube", forge: true });
  });

  it("Stop aborts the in-flight turn via the AbortController signal", async () => {
    const controller = new AbortController();
    const fetchFn = vi.fn().mockImplementation((_url: string, init: RequestInit) => {
      const signal = init.signal!;
      return new Promise((_resolve, reject) => {
        signal.addEventListener("abort", () =>
          reject(Object.assign(new Error("aborted"), { name: "AbortError" })),
        );
      });
    });
    vi.stubGlobal("fetch", fetchFn);

    const promise = api.streamChat(7, "a cube", () => {}, controller.signal);
    controller.abort();
    await expect(promise).resolves.toBeUndefined();
    expect(fetchFn.mock.calls[0][1].signal).toBe(controller.signal);
  });
});

describe("contributed request headers", () => {
  const withdrawals: (() => void)[] = [];

  function contribute(headers: Record<string, string>) {
    withdrawals.push(registerRequestHeaders(() => headers));
  }

  afterEach(() => {
    while (withdrawals.length) withdrawals.pop()?.();
    vi.unstubAllGlobals();
  });

  it("puts a contributed header on the app's own calls", async () => {
    // `req` is what every named endpoint below it goes through, so covering it
    // here covers all of them.
    contribute({ "X-Example": "one" });
    const fetchFn = mockFetch(200, []);

    await api.listProjects();

    expect(fetchFn.mock.calls[0][1].headers.get("x-example")).toBe("one");
  });

  it("keeps the JSON default beside a contributed header", async () => {
    contribute({ "X-Example": "one" });
    const fetchFn = mockFetch(200, []);

    await api.listProjects();

    expect(fetchFn.mock.calls[0][1].headers.get("content-type")).toBe("application/json");
  });

  it("lets the call's own header win the name it shares", async () => {
    // The order that keeps `importCatalog` working: a call that spelled a
    // header out meant that one, and a contributor must not be able to take a
    // route's content type away from it.
    // Contributed in the other case on purpose: an object merged by key keeps
    // both `content-type` and `Content-Type`, and the two values then reach the
    // server comma-joined under one name — which is a 422 rather than a
    // preference. The override has to answer to the name, not to the spelling.
    contribute({ "content-type": "application/json" });
    const fetchFn = mockFetch(200, { id: "l-bracket" });
    const file = new File([new Uint8Array([80, 75, 3, 4])], "l-bracket.cls");

    await api.importCatalog(file, "a".repeat(64));

    const headers = fetchFn.mock.calls[0][1].headers;
    expect(headers.get("content-type")).toBe("application/octet-stream");
    // And once. `Headers` joins repeats with a comma, so a surviving second
    // value shows up here rather than being invisible behind the first.
    expect(headers.get("content-type")).not.toContain(",");
  });

  it("puts a contributed header on the chat turn, which does not go through req", async () => {
    contribute({ "X-Example": "one" });
    const fetchFn = vi
      .fn()
      .mockResolvedValue(sseResponse(['data: {"event":"turn_end","stop_reason":"end_turn"}\n\n']));
    vi.stubGlobal("fetch", fetchFn);

    await api.streamChat(7, "a cube", () => {});

    const headers = fetchFn.mock.calls[0][1].headers;
    expect(headers.get("x-example")).toBe("one");
    expect(headers.get("content-type")).toBe("application/json");
  });

  it("keeps the chat turn's content type when a contributor spells it differently", async () => {
    // The turn passes no `init` of its own to reassert from, so it supplies its
    // content type as the call's own. Without that it would take a
    // contributor's and post a JSON body the server refuses to parse.
    contribute({ "content-type": "text/plain" });
    const fetchFn = vi
      .fn()
      .mockResolvedValue(sseResponse(['data: {"event":"turn_end","stop_reason":"end_turn"}\n\n']));
    vi.stubGlobal("fetch", fetchFn);

    await api.streamChat(7, "a cube", () => {});

    expect(fetchFn.mock.calls[0][1].headers.get("content-type")).toBe("application/json");
  });

  it("lets one contributor override another that spelled the name differently", async () => {
    // Both would otherwise survive a key merge and go out comma-joined, which
    // on a credential header means two keys on one request.
    contribute({ "X-Model-Key": "first" });
    contribute({ "x-model-key": "second" });
    const fetchFn = mockFetch(200, []);

    await api.listProjects();

    expect(fetchFn.mock.calls[0][1].headers.get("x-model-key")).toBe("second");
  });

  it("normalises a caller's Headers and a caller's array of pairs", async () => {
    // Both are legal `HeadersInit` and both arrive through the published
    // `request` export. Read with `Object.entries` they come back empty and a
    // caller's headers vanish with no error.
    contribute({ "X-Example": "contributed" });
    const fetchFn = mockFetch(200, []);

    await api.request("/projects", { headers: new Headers({ "X-Example": "from-headers" }) });
    expect(fetchFn.mock.calls[0][1].headers.get("x-example")).toBe("from-headers");

    await api.request("/projects", {
      headers: [
        ["X-Example", "a"],
        ["X-Example", "b"],
      ],
    });
    expect(fetchFn.mock.calls[1][1].headers.get("x-example")).toBe("a, b");
  });

  it("refuses a path that would send the request somewhere else", async () => {
    // A contributed credential rides every call, so an unrooted path is not a
    // typo — it is that credential leaving this origin.
    //
    // This build's `API_BASE` is absolute (`vite.config.ts` defaults it to
    // `http://localhost:8000`), so a path is refused here by failing to resolve
    // under that origin at all. The build where the guard has real work to do
    // is the same-origin one, and it is measured in `api.sameOrigin.test.ts`
    // rather than here, because `API_BASE` is settled per file.
    contribute({ "X-Model-Key": "a-secret-value" });
    const fetchFn = mockFetch(200, []);

    for (const path of ["https://elsewhere.example/x", "projects", ""]) {
      await expect(api.request(path)).rejects.toThrow("rooted at the API base");
    }

    expect(fetchFn).not.toHaveBeenCalled();
  });

  it("keeps a caller's own header value out of the refusal it throws", async () => {
    // `request` is published, so the value that trips the runtime need not be a
    // contributed one — a plugin passing its own key with a stray newline is
    // the same credential on the same screen. Handing the caller's headers to
    // `new Headers(...)` validated them all at once and raised the runtime's
    // message, which nothing on this side was holding.
    //
    // Asserted on the message this build writes rather than on the absence of
    // the secret from whatever was thrown: jsdom's own refusal quotes nothing,
    // so "does not contain the key" passes here with the guard removed. That is
    // measured, not supposed — a review round shipped exactly that assertion.
    // The redaction itself is measured against a quoting runtime in
    // `headers.test.ts`; what this one holds is that the caller's headers go
    // through the guard at all.
    const fetchFn = mockFetch(200, []);

    await expect(
      api.request("/projects", { headers: { Authorization: "Bearer sk-live-SECRETVALUE\r\nX: 1" } }),
    ).rejects.toThrow("a request header is not valid: Authorization");

    expect(fetchFn).not.toHaveBeenCalled();
  });

  it("keeps the caller's path out of the refusal it throws", async () => {
    // `errMessage` renders `Error.message` straight into a toast, so a message
    // that interpolated the path would put whatever the caller passed on the
    // screen — and a caller that got the path wrong is the one most likely to
    // have built it out of something that should not be read aloud.
    contribute({ "X-Model-Key": "a-secret-value" });
    mockFetch(200, []);

    await expect(api.request("https://elsewhere.example/x?token=a-secret-value")).rejects.toThrow(
      expect.objectContaining({ message: expect.not.stringContaining("a-secret-value") }),
    );
  });

  it("never smuggles a contributed header into a stream URL", async () => {
    // `EventSource` carries no custom header, and the reachable-looking way to
    // work around that is to put the value in the query string — where it lands
    // in every access log between here and the server.
    //
    // This one cannot fail against the code as it stands: `openStream` reads no
    // header state at all. It is here as a guard against the edit that adds it,
    // not as a check on the merge below.
    contribute({ "X-Example": "a-secret-value" });
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

    expect(captured[0].url).not.toContain("a-secret-value");
    expect(captured[0].url).not.toContain("X-Example");
  });

  it("stops sending a header once its source is withdrawn", async () => {
    const withdraw = registerRequestHeaders(() => ({ "X-Example": "one" }));
    withdraw();
    const fetchFn = mockFetch(200, []);

    await api.listProjects();

    expect(fetchFn.mock.calls[0][1].headers.get("x-example")).toBeNull();
  });
});
