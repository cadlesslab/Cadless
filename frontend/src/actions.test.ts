import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as actions from "./actions";
import * as api from "./api";
import { Store, initialState } from "./store";
import { viewportStore } from "./viewport/viewportStore";

vi.mock("./api", async (orig) => ({
  ...(await orig<typeof import("./api")>()),
  listProjects: vi.fn(async () => []),
  listVersions: vi.fn(async () => []),
  getMessages: vi.fn(async () => []),
  streamChat: vi.fn(),
  stopChat: vi.fn(),
}));

function previewing() {
  viewportStore.showPreview({ url: "/depot/artifacts/c/v/a", title: "Bracket" });
}

describe("returning to this machine's own work ends a preview", () => {
  beforeEach(() => viewportStore.clearPreview());

  it("opening a project stops showing the preview", async () => {
    previewing();
    await actions.selectProject(new Store(), 3);
    expect(viewportStore.get().preview).toBeNull();
  });

  it("picking a version stops showing the preview", () => {
    previewing();
    actions.showVersion(new Store(), 11);
    expect(viewportStore.get().preview).toBeNull();
  });

  it("versions arriving — as they do when a generation finishes — stops it too", async () => {
    previewing();
    await actions.loadVersions(new Store(), 3);
    expect(viewportStore.get().preview).toBeNull();
  });

  it("leaves the viewport alone when there was no preview to end", () => {
    let calls = 0;
    const unsub = viewportStore.subscribe(() => calls++);
    actions.showVersion(new Store(), 11);
    unsub();
    expect(calls).toBe(0);
  });
});

describe("Stop on a running chat turn", () => {
  let signal: AbortSignal | undefined;
  let endStream: () => void = () => {};

  beforeEach(() => {
    // Like the real read, this one ends when the server ends the turn or when it
    // is aborted, and not before.
    vi.mocked(api.streamChat).mockImplementation((_pid, _msg, _onEvent, sig) => {
      signal = sig;
      return new Promise<void>((resolve) => {
        endStream = resolve;
        sig?.addEventListener("abort", () => resolve());
      });
    });
    vi.mocked(api.stopChat).mockResolvedValue({ stopping: true });
  });
  afterEach(() => vi.clearAllMocks());

  function running(): Store {
    const store = new Store({ ...initialState, activeProjectId: 1 });
    actions.chat(store, "a cube");
    return store;
  }

  // Lets the stop request's answer land, and whatever it sets off run.
  const answered = () => new Promise((resolve) => setTimeout(resolve, 0));

  it("asks the server to stop the turn, and keeps reading it", async () => {
    const store = running();
    actions.stopChat(store);
    await answered();
    expect(api.stopChat).toHaveBeenCalledWith(1);
    expect(signal?.aborted).toBe(false);
  });

  it("says the turn is stopping from the first press until its stream ends", async () => {
    const store = running();
    actions.stopChat(store);
    await answered();
    expect(store.get().stopping).toBe(true);
    endStream();
    await answered();
    expect(store.get().stopping).toBe(false);
  });

  it("stops reading at once on a second press", async () => {
    const store = running();
    actions.stopChat(store);
    await answered();
    expect(signal?.aborted).toBe(false);
    actions.stopChat(store);
    expect(signal?.aborted).toBe(true);
  });

  it("stops reading at once when the server cannot take the request, as one from before the stop route", async () => {
    vi.mocked(api.stopChat).mockRejectedValueOnce(new api.ApiError(404, "Not Found"));
    const store = running();
    actions.stopChat(store);
    await answered();
    expect(signal?.aborted).toBe(true);
  });

  it("stops reading when the server has no turn left to stop", async () => {
    vi.mocked(api.stopChat).mockResolvedValueOnce({ stopping: false });
    const store = running();
    actions.stopChat(store);
    await answered();
    expect(signal?.aborted).toBe(true);
  });

  it("asks the project the turn runs in, though another is open now", async () => {
    const store = running();
    await actions.selectProject(store, 2);
    actions.stopChat(store);
    expect(api.stopChat).toHaveBeenCalledWith(1);
  });
});
