import { describe, expect, it } from "vitest";

import { glbUrl, type Version } from "../api";
import { readoutFor, viewerSubject } from "./preview";

function version(over: Partial<Version> = {}): Version {
  return {
    id: 7,
    project_id: 1,
    prompt: "a bracket",
    code: null,
    ok: true,
    error: null,
    volume: 120,
    bbox: [10, 20, 30],
    created_at: "2026-07-31T00:00:00Z",
    parameters: {},
    parent_version_id: null,
    plan_step: null,
    artifacts: [{ kind: "glb", path: "x.glb", size_bytes: 1 }],
    ...over,
  } as Version;
}

describe("viewerSubject", () => {
  it("draws the active version's mesh when nothing is being previewed", () => {
    const s = viewerSubject(version(), null, false);
    expect(s).toMatchObject({ url: glbUrl(7), previewing: false, emptyReason: null });
  });

  it("captures a thumbnail for the version it is actually showing", () => {
    expect(viewerSubject(version(), null, false).captureVersionId).toBe(7);
  });

  it("never captures a thumbnail while previewing", () => {
    // The capture is keyed by version id and kept, so a previewed mesh
    // rendered here would become the active version's picture everywhere it
    // is shown. Whether a preview is up decides this — not whether a version
    // happens to be open behind it.
    const s = viewerSubject(version(), { url: "/depot/artifacts/c/v/a", title: "Bracket" }, false);
    expect(s.captureVersionId).toBeNull();
  });

  it("a preview wins over the active version's mesh", () => {
    const s = viewerSubject(version(), { url: "/depot/artifacts/c/v/a", title: "Bracket" }, false);
    expect(s).toMatchObject({ url: "/depot/artifacts/c/v/a", previewing: true });
  });

  it("says why when the previewed catalogue has no mesh to show", () => {
    const s = viewerSubject(null, { url: null, title: "Gearbox", note: "Nothing baked" }, false);
    expect(s).toMatchObject({ url: null, previewing: true, emptyReason: "Nothing baked" });
  });

  it("has something to say even when the preview came without a note", () => {
    const s = viewerSubject(null, { url: null, title: "Gearbox" }, false);
    expect(s.emptyReason).toBeTruthy();
  });

  it("says why when a preview could not be loaded, rather than going blank", () => {
    const s = viewerSubject(null, { url: "/depot/artifacts/c/v/gone", title: "Gearbox" }, true);
    expect(s.url).toBeNull();
    expect(s.emptyReason).toMatch(/could not|failed/i);
  });

  it("says why when the active version's own mesh could not be loaded", () => {
    const s = viewerSubject(version(), null, true);
    expect(s.url).toBeNull();
    expect(s.emptyReason).toMatch(/could not|failed/i);
  });

  it("keeps what it said about a generation that failed", () => {
    const s = viewerSubject(version({ ok: false, artifacts: [] }), null, false);
    expect(s.emptyReason).toMatch(/Generation failed/);
  });

  it("keeps what it said when there is nothing open at all", () => {
    expect(viewerSubject(null, null, false).emptyReason).toMatch(/Generate a part/);
  });

  it("shows nothing for a version that finished without a mesh", () => {
    const s = viewerSubject(version({ artifacts: [] }), null, false);
    expect(s).toMatchObject({ url: null, captureVersionId: null });
  });
});

describe("readoutFor", () => {
  const SOMEWHERE = { url: "/depot/artifacts/c/v/a", title: "Bracket" };

  it("reads the active version's measurements off the active version", () => {
    expect(readoutFor(version(), 400, null)).toEqual({
      shown: true,
      bbox: [10, 20, 30],
      volume: 120,
      triangles: 400,
    });
  });

  it("drops measurements that describe something other than what is on screen", () => {
    // The triangle count is counted off the loaded mesh; the bounding box and
    // volume come from the active version. While a preview is up those are two
    // different objects, and printing them side by side is one wrong readout.
    const r = readoutFor(version(), 999, SOMEWHERE);
    expect(r.bbox).toBeNull();
    expect(r.volume).toBeNull();
    expect(r.triangles).toBe(999);
  });

  it("reads a preview even with no project open behind it", () => {
    expect(readoutFor(null, 999, SOMEWHERE)).toMatchObject({ shown: true, triangles: 999 });
  });

  it("stays quiet until the previewed mesh has been counted", () => {
    expect(readoutFor(version(), null, SOMEWHERE).shown).toBe(false);
  });

  it("counts nothing for a preview with nothing to draw", () => {
    // The count belongs to whatever was on screen before this one.
    const r = readoutFor(version(), 999, { url: null, title: "Gearbox", note: "no mesh" });
    expect(r).toMatchObject({ shown: false, triangles: null });
  });

  it("stays quiet when the active version failed", () => {
    expect(readoutFor(version({ ok: false }), 400, null).shown).toBe(false);
  });
});
