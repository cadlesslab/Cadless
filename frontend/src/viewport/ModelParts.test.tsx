import { useGLTF } from "@react-three/drei";
import { render } from "@testing-library/react";
import { Suspense } from "react";
import { describe, expect, it, vi } from "vitest";

import { ModelParts } from "./ModelParts";

// Every load stays pending, so nothing is drawn and what the test reads is which
// addresses were asked for: jsdom has no WebGL to draw them into.
vi.mock("@react-three/drei", () => ({
  useGLTF: vi.fn(() => {
    throw new Promise(() => {});
  }),
}));

describe("ModelParts", () => {
  it("asks for every part it is given", () => {
    render(
      <Suspense fallback={null}>
        <ModelParts urls={["/versions/7/artifacts/glb/0", "/versions/7/artifacts/glb/1"]} />
      </Suspense>,
    );
    const asked = new Set(vi.mocked(useGLTF).mock.calls.map(([url]) => url));
    expect(asked).toEqual(
      new Set(["/versions/7/artifacts/glb/0", "/versions/7/artifacts/glb/1"]),
    );
  });
});
