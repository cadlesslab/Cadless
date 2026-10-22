import { describe, expect, it } from "vitest";

import { artifactUrl, glbUrl, type Version, wholeAssemblyUrl } from "../api";
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
    artifacts: [{ kind: "glb", bytes: 1, part: 0 }],
    ...over,
  } as Version;
}

describe("viewerSubject", () => {
  it("draws the active version's mesh when nothing is being previewed", () => {
    const s = viewerSubject(version(), null, false);
    expect(s).toMatchObject({ urls: [glbUrl(7)], previewing: false, emptyReason: null });
  });

  it("draws every part of a version in several, and only its GLBs", () => {
    const artifacts = [0, 1].flatMap((part) =>
      (["glb", "step"] as const).map((kind) => ({ kind, bytes: 1, part })),
    );
    const s = viewerSubject(version({ artifacts }), null, false);
    expect(s.urls).toEqual([artifactUrl(7, "glb", 0), artifactUrl(7, "glb", 1)]);
  });

  it("names the part each address draws, in the order the addresses are drawn", () => {
    // Listed out of order on purpose: the rows arrive however the server
    // stored them, and an assembly names its parts by ordinal, not by position.
    const artifacts = [2, 0, 1].map((part) => ({ kind: "glb" as const, bytes: 1, part }));
    const s = viewerSubject(version({ artifacts }), null, false);
    expect(s.urls).toEqual([0, 1, 2].map((p) => artifactUrl(7, "glb", p)));
    expect(s.parts).toEqual([0, 1, 2]);
  });

  describe("a version with a whole-assembly file", () => {
    const rows = [
      ...[0, 1, 2].map((part) => ({ kind: "glb" as const, bytes: 1, part })),
      { kind: "assembly_glb", bytes: 1, part: 0 },
    ] as Version["artifacts"];

    it("names each part as the version does, for the file to be checked against", () => {
      const parts = [
        { index: 0, name: "chassis", path: [], color: null },
        { index: 2, name: "wheel", path: [], color: null },
      ];
      const s = viewerSubject(version({ artifacts: rows, parts }), null, false);
      expect(s.names).toEqual(["chassis", null, "wheel"]);
    });

    it("draws it from that one file, as the parts its files are numbered by", () => {
      const s = viewerSubject(version({ artifacts: rows }), null, false);
      expect(s).toMatchObject({
        urls: [wholeAssemblyUrl(7, "assembly_glb")],
        fromAssemblyFile: true,
      });
      expect(s.parts).toEqual([0, 1, 2]);
    });

    it("goes back to the file of each part when the whole one would not do", () => {
      const s = viewerSubject(version({ artifacts: rows }), null, false, true);
      expect(s.urls).toEqual([0, 1, 2].map((p) => artifactUrl(7, "glb", p)));
      expect(s.fromAssemblyFile).toBe(false);
    });

    it("is not used for a model printed whole", () => {
      const one = [
        { kind: "glb", bytes: 1, part: 0 },
        { kind: "assembly_glb", bytes: 1, part: 0 },
      ] as Version["artifacts"];
      expect(viewerSubject(version({ artifacts: one }), null, false)).toMatchObject({
        urls: [glbUrl(7)],
        fromAssemblyFile: false,
      });
    });
  });

  it("asks for each part of a version with no whole-assembly file, as before", () => {
    const artifacts = [0, 1].map((part) => ({ kind: "glb" as const, bytes: 1, part }));
    expect(viewerSubject(version({ artifacts }), null, false).fromAssemblyFile).toBe(false);
  });

  it("names no parts for a model drawn whole", () => {
    expect(viewerSubject(version(), null, false).parts).toEqual([]);
  });

  it("names no parts for a preview, even with a version in parts behind it", () => {
    const artifacts = [0, 1].map((part) => ({ kind: "glb" as const, bytes: 1, part }));
    const preview = { url: "/depot/artifacts/c/v/a", title: "Bracket" };
    expect(viewerSubject(version({ artifacts }), preview, false).parts).toEqual([]);
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
    expect(s).toMatchObject({ urls: ["/depot/artifacts/c/v/a"], previewing: true });
  });

  it("says why when the previewed catalogue has no mesh to show", () => {
    const s = viewerSubject(null, { url: null, title: "Gearbox", note: "Nothing baked" }, false);
    expect(s).toMatchObject({ urls: [], previewing: true, emptyReason: "Nothing baked" });
  });

  it("has something to say even when the preview came without a note", () => {
    const s = viewerSubject(null, { url: null, title: "Gearbox" }, false);
    expect(s.emptyReason).toBeTruthy();
  });

  it("says why when a preview could not be loaded, rather than going blank", () => {
    const s = viewerSubject(null, { url: "/depot/artifacts/c/v/gone", title: "Gearbox" }, true);
    expect(s.urls).toEqual([]);
    expect(s.emptyReason).toMatch(/could not|failed/i);
  });

  it("says why when the active version's own mesh could not be loaded", () => {
    const s = viewerSubject(version(), null, true);
    expect(s.urls).toEqual([]);
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
    expect(s).toMatchObject({ urls: [], captureVersionId: null });
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
