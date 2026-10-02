import { useGLTF } from "@react-three/drei";
import { renderHook, act } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { artifactUrl, type Version } from "../api";
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

    it("clears every part from the loader after a failure, so trying again loads them all", () => {
      const { result } = renderHook(() => usePreviewSubject(twoParts(), null));
      act(() => result.current.onFailed());
      expect(vi.mocked(useGLTF.clear).mock.calls).toEqual([
        [artifactUrl(7, "glb", 0)],
        [artifactUrl(7, "glb", 1)],
      ]);
    });

    it("does not take the same parts, asked for again, as a new attempt", () => {
      // The active version arrives as a new object on every refresh. Taking its
      // freshly built list as a new address would undo a failure as soon as it
      // was reported, and the loader would fail, clear and fetch again for as
      // long as the page stays open.
      const { result, rerender } = renderHook(
        ({ v }: { v: Version }) => usePreviewSubject(v, null),
        { initialProps: { v: twoParts() } },
      );
      act(() => result.current.onFailed());
      rerender({ v: twoParts() });
      expect(result.current.subject.urls).toEqual([]);
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
