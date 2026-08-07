import { beforeEach, describe, expect, it, vi } from "vitest";

import * as actions from "./actions";
import { Store } from "./store";
import { viewportStore } from "./viewport/viewportStore";

vi.mock("./api", () => ({
  listProjects: vi.fn(async () => []),
  listVersions: vi.fn(async () => []),
  getMessages: vi.fn(async () => []),
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
