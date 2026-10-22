import { useGLTF } from "@react-three/drei";
import { render } from "@testing-library/react";
import { Suspense } from "react";
import * as THREE from "three";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { encodeGlb, parseGlb } from "../test/glb";
import { ModelParts } from "./ModelParts";

const PENDING = () => {
  throw new Promise(() => {});
};

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

describe("ModelParts drawing a whole-assembly file", () => {
  // jsdom draws three's elements as unknown tags and says so; what is read here
  // is what is handed up and let go of, not what is drawn.
  beforeEach(() => vi.spyOn(console, "error").mockImplementation(() => {}));
  afterEach(() => {
    vi.mocked(console.error).mockRestore();
    vi.mocked(useGLTF).mockImplementation(PENDING as never);
  });

  /** A loaded file of two parts, `a` and `b`, drawn as `parts` named `names`. */
  async function drawn(parts: number[], names: (string | null)[]) {
    const gltf = await parseGlb(
      encodeGlb([
        { name: "a", primitives: [new THREE.BoxGeometry()] },
        { name: "b", primitives: [new THREE.BoxGeometry()] },
      ]),
    );
    vi.mocked(useGLTF).mockImplementation((() => gltf) as never);
    const onPartGroup = vi.fn();
    const onUnusable = vi.fn();
    const view = render(
      <Suspense fallback={null}>
        <ModelParts
          urls={["/x"]}
          parts={parts}
          fromAssemblyFile
          names={names}
          onPartGroup={onPartGroup}
          onUnusable={onUnusable}
        />
      </Suspense>,
    );
    return { ...view, onPartGroup, onUnusable };
  }

  const handedUp = (spy: ReturnType<typeof vi.fn>, present: boolean) =>
    spy.mock.calls.filter(([, group]) => (group !== null) === present).map(([part]) => part).sort();

  it("hands each part up by ordinal, and lets go of each when it goes", async () => {
    const { onPartGroup, unmount } = await drawn([0, 1], ["a", "b"]);
    expect(handedUp(onPartGroup, true)).toEqual([0, 1]);
    onPartGroup.mockClear();
    unmount();
    expect(handedUp(onPartGroup, false)).toEqual([0, 1]);
  });

  it("lets go of the materials it made when it goes", async () => {
    const dispose = vi.spyOn(THREE.Material.prototype, "dispose");
    try {
      const { unmount } = await drawn([0, 1], ["a", "b"]);
      dispose.mockClear();
      unmount();
      expect(dispose).toHaveBeenCalled();
    } finally {
      dispose.mockRestore();
    }
  });

  it("says so when the file does not hold the parts it is drawn as", async () => {
    // Loaded and readable, and still not this version's model: two parts where
    // the version numbers three. Drawn anyway, the player would move the wrong
    // piece; drawn as nothing and said nothing, the viewport would stay blank.
    const { onUnusable, onPartGroup } = await drawn([0, 1, 2], [null, null, null]);
    expect(onUnusable).toHaveBeenCalledTimes(1);
    expect(onPartGroup).not.toHaveBeenCalled();
  });

  it("says so when the file's parts are named otherwise than the version's", async () => {
    const { onUnusable, onPartGroup } = await drawn([0, 1], ["b", "a"]);
    expect(onUnusable).toHaveBeenCalledTimes(1);
    expect(onPartGroup).not.toHaveBeenCalled();
  });
});
