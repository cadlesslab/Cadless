import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { NARROW_QUERY, useNarrow } from "./useNarrow";

/** A matchMedia whose answer this test controls, plus a way to change it the
 *  way a browser does — by firing `change` at whoever subscribed. */
function stubMatchMedia(matches: boolean) {
  const listeners = new Set<(e: MediaQueryListEvent) => void>();
  const asked: string[] = [];
  const mql = {
    get matches() {
      return matches;
    },
    media: NARROW_QUERY,
    onchange: null,
    addEventListener: (_: string, fn: (e: MediaQueryListEvent) => void) => listeners.add(fn),
    removeEventListener: (_: string, fn: (e: MediaQueryListEvent) => void) => listeners.delete(fn),
    dispatchEvent: () => false,
  };
  vi.stubGlobal("matchMedia", (query: string) => {
    asked.push(query);
    return mql;
  });
  return {
    asked,
    listenerCount: () => listeners.size,
    resize(next: boolean) {
      matches = next;
      for (const fn of listeners) fn({ matches: next } as MediaQueryListEvent);
    },
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("useNarrow", () => {
  it("answers from the first paint rather than after one", () => {
    stubMatchMedia(true);
    const { result } = renderHook(() => useNarrow());
    // Not `false` then `true`: a hook that started wide would render the
    // desktop layout for one frame on every phone that loads the app.
    expect(result.current).toBe(true);
  });

  it("follows the viewport when it changes", () => {
    const media = stubMatchMedia(false);
    const { result } = renderHook(() => useNarrow());
    expect(result.current).toBe(false);
    act(() => media.resize(true));
    expect(result.current).toBe(true);
    act(() => media.resize(false));
    expect(result.current).toBe(false);
  });

  it("asks about the width the stylesheet breaks at", () => {
    const media = stubMatchMedia(false);
    renderHook(() => useNarrow());
    // The hook and app.css have to break at the same width; asserting the
    // constant here is what makes a change to one show up as a failure.
    expect(media.asked).toContain("(max-width: 720px)");
  });

  it("unsubscribes when it goes away", () => {
    const media = stubMatchMedia(false);
    const { unmount } = renderHook(() => useNarrow());
    expect(media.listenerCount()).toBe(1);
    unmount();
    expect(media.listenerCount()).toBe(0);
  });
});
