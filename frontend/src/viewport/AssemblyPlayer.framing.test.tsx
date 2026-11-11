/** Where the camera goes while an assembly plays apart and back together.
 *
 * There is no WebGL here, so drei's `<Bounds>` is stood in for by the part of it
 * the player drives, applied at once rather than animated: a perspective `fit`
 * is drei's `reset` — the camera put on the line from the box's centre through
 * where it stands, at the distance that fits the box — an orthographic `fit`
 * changes only the zoom, and `moveTo`/`lookAt` set the end of the move. That is
 * drei 9.122.0's own arithmetic, which is what decides where a reader is left
 * looking. */
import { act, render } from "@testing-library/react";
import * as THREE from "three";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const FOV = 50;
const MARGIN = 1.25;

let frame: ((state: unknown, delta: number) => void) | null = null;
let camera: THREE.PerspectiveCamera | THREE.OrthographicCamera;
let controls: THREE.EventDispatcher<{ start: object }> & { target: THREE.Vector3; maxDistance: number; update: () => void };

function standInBounds() {
  const box = new THREE.Box3();
  const ortho = () => (camera as THREE.OrthographicCamera).isOrthographicCamera === true;
  const getSize = () => {
    const size = box.getSize(new THREE.Vector3());
    const center = box.getCenter(new THREE.Vector3());
    const largest = Math.max(size.x, size.y, size.z);
    if (ortho()) return { box, size, center, distance: MARGIN * largest * 4 };
    const perspective = camera as THREE.PerspectiveCamera;
    const fitHeight = largest / (2 * Math.atan((Math.PI * perspective.fov) / 360));
    const fitWidth = fitHeight / perspective.aspect;
    return { box, size, center, distance: MARGIN * Math.max(fitHeight, fitWidth) };
  };
  const api = {
    getSize,
    refresh(object?: THREE.Box3) {
      if (object) box.copy(object);
      return api;
    },
    clip() {
      const { distance } = getSize();
      camera.near = distance / 100;
      camera.far = distance * 100;
      camera.updateProjectionMatrix();
      controls.maxDistance = distance * 10;
      return api;
    },
    reset() {
      const { center, distance } = getSize();
      const direction = camera.position.clone().sub(center).normalize();
      camera.position.copy(center).addScaledVector(direction, distance);
      controls.target.copy(center);
      camera.lookAt(center);
      return api;
    },
    fit() {
      // drei: "For non-orthographic cameras, fit should behave exactly like reset".
      if (!ortho()) return api.reset();
      // Orthographic: the zoom that fits the box's corners as seen from where the
      // camera stands, looking where it looks.
      const view = camera as THREE.OrthographicCamera;
      const toCamera = new THREE.Matrix4()
        .lookAt(view.position, controls.target, view.up)
        .setPosition(view.position)
        .invert();
      let height = 0;
      let width = 0;
      for (const x of [box.min.x, box.max.x])
        for (const y of [box.min.y, box.max.y])
          for (const z of [box.min.z, box.max.z]) {
            const corner = new THREE.Vector3(x, y, z).applyMatrix4(toCamera);
            height = Math.max(height, Math.abs(corner.y));
            width = Math.max(width, Math.abs(corner.x));
          }
      view.zoom = Math.min((view.top - view.bottom) / (2 * height), (view.right - view.left) / (2 * width)) / MARGIN;
      view.updateProjectionMatrix();
      return api;
    },
    moveTo(position: THREE.Vector3) {
      camera.position.copy(position);
      return api;
    },
    lookAt({ target }: { target: THREE.Vector3 }) {
      controls.target.copy(target);
      camera.lookAt(target);
      return api;
    },
  };
  return api;
}

let bounds: ReturnType<typeof standInBounds>;

vi.mock("@react-three/fiber", () => ({
  useFrame: (callback: (state: unknown, delta: number) => void) => {
    frame = callback;
  },
  useThree: (select: (state: unknown) => unknown) => select({ camera, controls }),
}));
vi.mock("@react-three/drei", () => ({ useBounds: () => bounds }));

import { AssemblyPlayer } from "./AssemblyPlayer";
import { parseMotion } from "./assemblyMotion";
import { viewportStore } from "./viewportStore";

/** A unit cube centred at `x` inside a part's own group. */
function part(x: number): THREE.Group {
  const group = new THREE.Group();
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), new THREE.MeshBasicMaterial());
  mesh.position.set(x, 0, 0);
  group.add(mesh);
  return group;
}

// Two cubes side by side, spanning x from -0.5 to 1.5, the second leaving
// upward (CAD +Z is scene +Y): apart, the extent's centre rises above the one
// the reader was looking at.
function mount() {
  const model = new THREE.Group();
  const [p0, p1] = [part(0), part(1)];
  model.add(p0, p1);
  const motion = parseMotion(
    { order: [0, 1], releases: [[], [0, 0, 1]], stretch: [1, 1], explode_fraction: 0.35, travel: [0, 1.5] },
    [0, 1],
  )!;
  return render(
    <AssemblyPlayer
      motion={motion}
      modelRef={{ current: model }}
      partGroups={{ current: new Map([[0, p0], [1, p1]]) }}
      onApartBox={() => {}}
    />,
  );
}

/** Where the reader looks from, as a unit direction out of what they look at. */
function viewDirection(): THREE.Vector3 {
  return camera.position.clone().sub(controls.target).normalize();
}

function playTo(position: number) {
  act(() => viewportStore.setAssemblyPosition(position));
  frame?.(null, 0.016);
}

beforeEach(() => {
  frame = null;
  camera = new THREE.PerspectiveCamera(FOV, 1.5, 0.1, 1000);
  controls = Object.assign(new THREE.EventDispatcher<{ start: object }>(), {
    target: new THREE.Vector3(0.5, 0, 0),
    maxDistance: Infinity,
    update: () => {},
  });
  camera.position.set(3.5, 3, 3);
  camera.lookAt(controls.target);
  bounds = standInBounds();
});

afterEach(() => {
  act(() => viewportStore.setAssemblyPosition(0));
});

describe("AssemblyPlayer framing", () => {
  it("keeps the reader's angle while it widens to the parts apart", () => {
    mount();
    const before = viewDirection();

    playTo(1);

    expect(viewDirection().angleTo(before)).toBeLessThan(1e-6);
    // Aimed at the parts apart — 3 units up, so x -0.5..1.5, y -0.5..3.5,
    // z -0.5..0.5 — from the distance that fits their largest side, 4.
    expect(controls.target.distanceTo(new THREE.Vector3(0.5, 1.5, 0))).toBeLessThan(1e-6);
    const fitHeight = 4 / (2 * Math.atan((Math.PI * FOV) / 360));
    const fits = MARGIN * Math.max(fitHeight, fitHeight / (camera as THREE.PerspectiveCamera).aspect);
    expect(camera.position.distanceTo(controls.target)).toBeCloseTo(fits, 6);
  });

  it("goes back to where the reader was once the parts are together again", () => {
    mount();
    const position = camera.position.clone();
    const target = controls.target.clone();

    playTo(1);
    playTo(0);

    expect(camera.position.distanceTo(position)).toBeLessThan(1e-6);
    expect(controls.target.distanceTo(target)).toBeLessThan(1e-6);
  });

  it("zooms an orthographic camera back to the model once the parts are home", () => {
    camera = new THREE.OrthographicCamera(-3, 3, 2, -2, 0.1, 1000);
    camera.position.set(3.5, 3, 3);
    camera.lookAt(controls.target);
    bounds = standInBounds();
    // The fit drei's <Bounds> makes of the model as it loads.
    bounds.refresh(new THREE.Box3(new THREE.Vector3(-0.5, -0.5, -0.5), new THREE.Vector3(1.5, 0.5, 0.5))).fit();
    const zoom = camera.zoom;
    const before = viewDirection();
    mount();

    playTo(1);
    expect(camera.zoom).toBeLessThan(zoom);
    expect(viewDirection().angleTo(before)).toBeLessThan(1e-6);
    playTo(0);

    expect(camera.zoom).toBeCloseTo(zoom, 9);
  });

  it("leaves a view the reader asked for while apart", () => {
    mount();
    playTo(1);
    // What the view toolbar does; the rig moves the camera for it.
    act(() => viewportStore.view("top"));
    camera.position.set(0.5, 6, 0.001);
    controls.target.set(0.5, 1.5, 0);
    const theirs = camera.position.clone();

    playTo(0);

    expect(camera.position.distanceTo(theirs)).toBeLessThan(1e-6);
  });

  it("leaves the reader's own view alone if they moved the camera while apart", () => {
    mount();
    playTo(1);
    // The reader takes hold of the camera and puts it somewhere of their own.
    controls.dispatchEvent({ type: "start" });
    camera.position.set(-4, 1, 2);
    controls.target.set(0, 0.5, 0);
    const theirs = camera.position.clone();

    playTo(0);

    expect(camera.position.distanceTo(theirs)).toBeLessThan(1e-6);
  });
});
