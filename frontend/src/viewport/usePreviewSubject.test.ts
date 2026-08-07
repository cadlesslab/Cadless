import { renderHook, act } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { Version } from "../api";
import { usePreviewSubject } from "./usePreviewSubject";
import type { Preview } from "./viewportStore";

vi.mock("@react-three/drei", () => ({ useGLTF: { clear: vi.fn() } }));

function version(over: Partial<Version> = {}): Version {
  return {
    id: 7,
    ok: true,
    volume: 120,
    bbox: [10, 20, 30],
    artifacts: [{ kind: "glb", path: "x.glb", size_bytes: 1 }],
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
    expect(result.current.subject.url).toBeNull();
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
    expect(result.current.subject.url).toBeNull();

    rerender({ preview: { ...SOMEWHERE } });
    expect(result.current.subject.url).toBe(SOMEWHERE.url);
  });

  it("does not hold a failure against the next catalogue", () => {
    const { result, rerender } = renderHook(
      ({ preview }: { preview: Preview }) => usePreviewSubject(version(), preview),
      { initialProps: { preview: SOMEWHERE } },
    );
    act(() => result.current.onFailed());

    rerender({ preview: { url: "/depot/artifacts/c2/v2/a2", title: "Gearbox" } });
    expect(result.current.subject.url).toBe("/depot/artifacts/c2/v2/a2");
  });

  it("keeps its answers stable when nothing has happened", () => {
    const { result, rerender } = renderHook(() => usePreviewSubject(version(), null));
    const first = result.current.subject.url;
    rerender();
    expect(result.current.subject.url).toBe(first);
  });
});
