import { afterEach, describe, expect, it, vi } from "vitest";

import * as api from "./api";

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
    expect(init.headers["Content-Type"]).toBe("application/octet-stream");
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
