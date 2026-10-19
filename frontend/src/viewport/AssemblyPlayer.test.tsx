/** The player against real three.js groups and the real store.
 *
 * There is no WebGL here, so the r3f hooks the player leans on are stood in for:
 * `useFrame` hands its callback to the test to call, and `useBounds` answers its
 * framing calls and does nothing. Everything the player moves is a real group,
 * and the section it has to stay out of the way of is the real one. */
import { act, render } from "@testing-library/react";
import * as THREE from "three";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

let frame: ((state: unknown, delta: number) => void) | null = null;
vi.mock("@react-three/fiber", () => ({
  useFrame: (callback: (state: unknown, delta: number) => void) => {
    frame = callback;
  },
  useThree: (select: (state: unknown) => unknown) => select({ gl: { localClippingEnabled: false } }),
}));
vi.mock("@react-three/drei", () => ({
  useBounds: () => {
    const api = { refresh: () => api, clip: () => api, fit: () => api };
    return api;
  },
}));

import { AssemblyPlayer } from "./AssemblyPlayer";
import { parseMotion } from "./assemblyMotion";
import { SectionPlane } from "./inspectionScene";
import { viewportStore } from "./viewportStore";

/** A unit cube centred at `x` inside a part's own group. */
function part(x: number): THREE.Group {
  const group = new THREE.Group();
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), new THREE.MeshBasicMaterial());
  mesh.position.set(x, 0, 0);
  group.add(mesh);
  return group;
}

// Two cubes side by side, spanning x from -0.5 to 1.5; part 1 was released
// along `heading` in CAD axes, CAD +X unless a test says otherwise. Travel is
// 0.35 of the 2-unit length.
const TRAVEL = 0.7;

function mount(heading: number[] = [1, 0, 0], measured: Record<string, unknown> = {}) {
  const model = new THREE.Group();
  const [p0, p1] = [part(0), part(1)];
  model.add(p0, p1);
  const motion = parseMotion(
    { order: [0, 1], releases: [[], heading], stretch: [1, 1], explode_fraction: 0.35, ...measured },
    [0, 1],
  )!;
  const view = render(
    <>
      <AssemblyPlayer
        motion={motion}
        modelRef={{ current: model }}
        partGroups={{ current: new Map([[0, p0], [1, p1]]) }}
        onApartBox={() => {}}
      />
      <SectionPlane modelRef={{ current: model }} />
    </>,
  );
  return { p0, p1, view };
}

beforeEach(() => {
  frame = null;
  act(() => viewportStore.setTool("none"));
});

afterEach(() => {
  act(() => viewportStore.setTool("none"));
});

describe("AssemblyPlayer", () => {
  it("moves a part's own group along its heading as soon as the position changes", () => {
    // Released along CAD +Z, so it rises along scene +Y.
    const { p0, p1 } = mount([0, 0, 1]);
    act(() => viewportStore.setAssemblyPosition(1));
    expect(p1.position.x).toBeCloseTo(0, 9);
    expect(p1.position.y).toBeCloseTo(TRAVEL, 9);
    expect(p1.position.z).toBeCloseTo(0, 9);
    expect(p0.position.toArray()).toEqual([0, 0, 0]);
  });

  it("moves a part by the travel the engine measured, scaled to the model drawn", () => {
    // Measured as 1.5 of the model's largest dimension, which is 2 units here.
    const { p1 } = mount([0, 0, 1], { travel: [0, 1.5] });
    act(() => viewportStore.setAssemblyPosition(1));
    expect(p1.position.y).toBeCloseTo(3, 9);
  });

  it("cuts the assembled model when section is chosen while the parts are apart", () => {
    // A section chosen mid-play is measured the moment it is chosen. Measured
    // before the parts were home, the cut sat where they had reached, and stayed
    // there until the axis or offset was touched.
    const { p0 } = mount();
    act(() => viewportStore.setSectionAxis("x"));
    act(() => viewportStore.setSectionOffset(0));
    act(() => viewportStore.setAssemblyPosition(1));
    frame?.(null, 0.016);
    act(() => viewportStore.setTool("section"));
    const material = (p0.children[0] as THREE.Mesh).material as THREE.Material;
    // The assembled model spans x in [-0.5, 1.5], so offset 0 cuts at its centre.
    expect(-material.clippingPlanes![0].constant).toBeCloseTo(0.5, 6);
  });

  it("puts every part home and hides the control when it goes away", () => {
    const { p1, view } = mount();
    expect(viewportStore.get().assemblySteps).toBe(1);
    act(() => viewportStore.setAssemblyPosition(1));
    view.unmount();
    expect(p1.position.x).toBe(0);
    expect(viewportStore.get().assemblySteps).toBe(0);
    // The next model's play must not reach these groups through a listener
    // left behind.
    act(() => {
      viewportStore.setAssemblySteps(1);
      viewportStore.setAssemblyPosition(1);
    });
    expect(p1.position.x).toBe(0);
    act(() => viewportStore.setAssemblySteps(0));
  });
});
