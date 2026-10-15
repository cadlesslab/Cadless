import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { BASE, type AssemblyMotion, type Version } from "../api";
import { useAssemblyMotion } from "./useAssemblyMotion";

const PARTS = [0, 1];
const PAYLOAD: AssemblyMotion = {
  order: [0, 1],
  releases: [[], [0, 0, 1]],
  stretch: [1, 1],
  explode_fraction: 0.35,
};

/** A version drawn in two parts, carrying the motion unless told otherwise. */
function version(id: number, carriesMotion = true): Version {
  const rows = [0, 1].map((part) => ({ kind: "glb", bytes: 1, part }));
  if (carriesMotion) rows.push({ kind: "assembly", bytes: 1, part: 0 });
  return { id, ok: true, artifacts: rows } as unknown as Version;
}

function answer(status: number, body: unknown) {
  return { ok: status >= 200 && status < 300, status, statusText: "x", json: async () => body };
}

function serve(status: number, body: unknown) {
  const fetchFn = vi.fn().mockResolvedValue(answer(status, body));
  vi.stubGlobal("fetch", fetchFn);
  return fetchFn;
}

/** Let every answer already given reach the hook. */
const settle = () => act(() => new Promise((resolve) => setTimeout(resolve, 0)));

afterEach(() => vi.unstubAllGlobals());

describe("useAssemblyMotion", () => {
  it("asks for nothing when the version carries no motion", async () => {
    const fetchFn = serve(200, PAYLOAD);
    const { result } = renderHook(() => useAssemblyMotion(version(7, false), false, PARTS));
    await settle();
    expect(fetchFn).not.toHaveBeenCalled();
    expect(result.current).toBeNull();
  });

  it("asks for nothing while something else is previewed", async () => {
    const fetchFn = serve(200, PAYLOAD);
    const { result } = renderHook(() => useAssemblyMotion(version(7), true, []));
    await settle();
    expect(fetchFn).not.toHaveBeenCalled();
    expect(result.current).toBeNull();
  });

  it("plays the motion the version carries", async () => {
    serve(200, PAYLOAD);
    const { result } = renderHook(() => useAssemblyMotion(version(7), false, PARTS));
    await waitFor(() => expect(result.current).not.toBeNull());
    expect(result.current!.order).toEqual([0, 1]);
  });

  it("offers no control for a payload that does not describe the parts drawn", async () => {
    const fetchFn = serve(200, { ...PAYLOAD, order: [0, 1, 2] });
    const { result } = renderHook(() => useAssemblyMotion(version(7), false, PARTS));
    await waitFor(() => expect(fetchFn).toHaveBeenCalled());
    await settle();
    expect(result.current).toBeNull();
  });

  it("drops the last version's motion when the next one's cannot be read", async () => {
    // A missing file is no control rather than an error on screen: the model
    // is still drawn, just not played.
    serve(200, PAYLOAD);
    const { result, rerender } = renderHook(
      ({ v }: { v: Version }) => useAssemblyMotion(v, false, PARTS),
      { initialProps: { v: version(7) } },
    );
    await waitFor(() => expect(result.current).not.toBeNull());

    const fetchFn = serve(404, { detail: "Not Found" });
    rerender({ v: version(8) });
    // Not even for the moment before the next answer arrives: version 7's
    // motion played over version 8's parts would move them by the wrong list.
    expect(result.current).toBeNull();
    await waitFor(() => expect(fetchFn).toHaveBeenCalled());
    await settle();
    expect(result.current).toBeNull();
  });

  it("asks again for a new version, and ignores what arrives late for the old one", async () => {
    const late: ((value: unknown) => void)[] = [];
    const fetchFn = vi.fn((url: string) =>
      url.endsWith("/versions/7/artifacts/assembly")
        ? new Promise((resolve) => late.push(resolve))
        : Promise.resolve(answer(200, { ...PAYLOAD, order: [1, 0] })),
    );
    vi.stubGlobal("fetch", fetchFn);
    const { result, rerender } = renderHook(
      ({ v }: { v: Version }) => useAssemblyMotion(v, false, PARTS),
      { initialProps: { v: version(7) } },
    );
    await waitFor(() => expect(late).toHaveLength(1));

    rerender({ v: version(8) });
    await waitFor(() => expect(result.current?.order).toEqual([1, 0]));

    late[0](answer(200, PAYLOAD));
    await settle();
    expect(result.current?.order).toEqual([1, 0]);
    expect(fetchFn.mock.calls.map(([url]) => url)).toEqual([
      `${BASE}/versions/7/artifacts/assembly`,
      `${BASE}/versions/8/artifacts/assembly`,
    ]);
  });

  it("hands back the same motion while nothing about it has changed", async () => {
    // The player measures the model and resets the play whenever the motion it
    // is given changes, so a motion rebuilt on every render would hold the
    // parts at rest. The parts arrive as a fresh list on every render.
    serve(200, PAYLOAD);
    const { result, rerender } = renderHook(
      ({ parts }: { parts: number[] }) => useAssemblyMotion(version(7), false, parts),
      { initialProps: { parts: [...PARTS] } },
    );
    await waitFor(() => expect(result.current).not.toBeNull());
    const first = result.current;
    rerender({ parts: [...PARTS] });
    expect(result.current).toBe(first);
  });
});
