import { useGLTF } from "@react-three/drei";
import { renderHook, act } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { artifactUrl, type Version, wholeAssemblyUrl } from "../api";
import { usePreviewSubject } from "./usePreviewSubject";
import type { Preview } from "./viewportStore";

vi.mock("@react-three/drei", () => ({ useGLTF: { clear: vi.fn() } }));

function version(over: Partial<Version> = {}): Version {
  return {
    id: 7,
    ok: true,
    volume: 120,
    bbox: [10, 20, 30],
    artifacts: [{ kind: "glb", bytes: 1, part: 0 }],
    ...over,
  } as Version;
}

const SOMEWHERE: Preview = { url: "/depot/artifacts/c/v/a", title: "Bracket" };

describe("usePreviewSubject", () => {
  it("shows the active version until something else is put in front of it", () => {
    const { result, rerender } = renderHook(
      ({ preview }: { preview: Preview | null }) => usePreviewSubject(version(), preview),
      { initialProps: { preview: null as Preview | null } },
    );
    expect(result.current.subject.captureVersionId).toBe(7);

    rerender({ preview: SOMEWHERE });
    expect(result.current.subject).toMatchObject({ previewing: true, captureVersionId: null });
  });

  it("says why after a failure instead of leaving the viewport blank", () => {
    const { result } = renderHook(() => usePreviewSubject(version(), SOMEWHERE));
    act(() => result.current.onFailed());
    expect(result.current.subject.urls).toEqual([]);
    expect(result.current.subject.emptyReason).toBeTruthy();
  });

  it("lets the same catalogue be tried again after it failed once", () => {
    // Opening the same card twice is a new attempt at the same address, and a
    // rule keyed only on the address would never hear about the second.
    const { result, rerender } = renderHook(
      ({ preview }: { preview: Preview }) => usePreviewSubject(version(), preview),
      { initialProps: { preview: SOMEWHERE } },
    );
    act(() => result.current.onFailed());
    expect(result.current.subject.urls).toEqual([]);

    rerender({ preview: { ...SOMEWHERE } });
    expect(result.current.subject.urls).toEqual([SOMEWHERE.url]);
  });

  it("does not hold a failure against the next catalogue", () => {
    const { result, rerender } = renderHook(
      ({ preview }: { preview: Preview }) => usePreviewSubject(version(), preview),
      { initialProps: { preview: SOMEWHERE } },
    );
    act(() => result.current.onFailed());

    rerender({ preview: { url: "/depot/artifacts/c2/v2/a2", title: "Gearbox" } });
    expect(result.current.subject.urls).toEqual(["/depot/artifacts/c2/v2/a2"]);
  });

  it("keeps its answers stable when nothing has happened", () => {
    const { result, rerender } = renderHook(() => usePreviewSubject(version(), null));
    const first = result.current.subject.urls;
    rerender();
    expect(result.current.subject.urls).toEqual(first);
  });

  describe("a version in several parts", () => {
    const twoParts = (id = 7) =>
      version({ id, artifacts: [0, 1].map((part) => ({ kind: "glb" as const, bytes: 1, part })) });

    beforeEach(() => vi.mocked(useGLTF.clear).mockClear());

    it("clears every part on screen after a failure, so trying again loads them all", () => {
      // Opened on nothing, then given the parts: a failure clears what is on
      // screen now, not what was there when the hook first rendered.
      const { result, rerender } = renderHook(
        ({ v }: { v: Version | null }) => usePreviewSubject(v, null),
        { initialProps: { v: null as Version | null } },
      );
      rerender({ v: twoParts() });
      act(() => result.current.onFailed());
      expect(vi.mocked(useGLTF.clear).mock.calls).toEqual([
        [artifactUrl(7, "glb", 0)],
        [artifactUrl(7, "glb", 1)],
      ]);
    });

    it("does not take the same parts, asked for again, as a new attempt", () => {
      // A fresh object holding the same parts must not count as a new attempt:
      // counted as one, it would undo a failure as soon as it was reported, and
      // the loader would fail, clear and fetch again for as long as the page
      // stays open.
      const { result, rerender } = renderHook(
        ({ v }: { v: Version }) => usePreviewSubject(v, null),
        { initialProps: { v: twoParts() } },
      );
      act(() => result.current.onFailed());
      rerender({ v: twoParts() });
      expect(result.current.subject.urls).toEqual([]);
    });

    it("falls back to the file of each part when the whole-assembly file fails", () => {
      const withWhole = version({
        artifacts: [
          ...[0, 1].map((part) => ({ kind: "glb" as const, bytes: 1, part })),
          { kind: "assembly_glb", bytes: 1, part: 0 },
        ] as Version["artifacts"],
      });
      const { result } = renderHook(() => usePreviewSubject(withWhole, null));
      expect(result.current.subject.urls).toEqual([wholeAssemblyUrl(7, "assembly_glb")]);

      act(() => result.current.onFailed());
      expect(vi.mocked(useGLTF.clear).mock.calls).toEqual([[wholeAssemblyUrl(7, "assembly_glb")]]);
      expect(result.current.subject.urls).toEqual([artifactUrl(7, "glb", 0), artifactUrl(7, "glb", 1)]);
      expect(result.current.subject.emptyReason).toBeNull();

      // Only when those fail too is the model one that could not be loaded.
      act(() => result.current.onFailed());
      expect(result.current.subject.urls).toEqual([]);
      expect(result.current.subject.emptyReason).toBeTruthy();
    });

    it("asks a later version for its own whole-assembly file after one would not do", () => {
      const withWhole = (id: number) =>
        version({
          id,
          artifacts: [
            ...[0, 1].map((part) => ({ kind: "glb" as const, bytes: 1, part })),
            { kind: "assembly_glb", bytes: 1, part: 0 },
          ] as Version["artifacts"],
        });
      const seen: string[][] = [];
      const { result, rerender } = renderHook(
        ({ v }: { v: Version }) => {
          const r = usePreviewSubject(v, null);
          seen.push(r.subject.urls);
          return r;
        },
        { initialProps: { v: withWhole(7) } },
      );
      act(() => result.current.onFailed());
      seen.length = 0;
      rerender({ v: withWhole(8) });
      // Its very first render already asks for the whole file, not its parts.
      expect(seen[0]).toEqual([wholeAssemblyUrl(8, "assembly_glb")]);
    });

    it("tries a version's whole-assembly file again when it is opened again", () => {
      const withWhole = (id: number) =>
        version({
          id,
          artifacts: [
            ...[0, 1].map((part) => ({ kind: "glb" as const, bytes: 1, part })),
            { kind: "assembly_glb", bytes: 1, part: 0 },
          ] as Version["artifacts"],
        });
      const { result, rerender } = renderHook(
        ({ v }: { v: Version }) => usePreviewSubject(v, null),
        { initialProps: { v: withWhole(7) } },
      );
      act(() => result.current.onFailed());
      rerender({ v: withWhole(8) });
      rerender({ v: withWhole(7) });
      expect(result.current.subject.urls).toEqual([wholeAssemblyUrl(7, "assembly_glb")]);
    });

    it("tries again for a version with other parts", () => {
      const { result, rerender } = renderHook(
        ({ v }: { v: Version }) => usePreviewSubject(v, null),
        { initialProps: { v: twoParts() } },
      );
      act(() => result.current.onFailed());
      rerender({ v: twoParts(8) });
      expect(result.current.subject.urls).toEqual([
        artifactUrl(8, "glb", 0),
        artifactUrl(8, "glb", 1),
      ]);
    });
  });
});
