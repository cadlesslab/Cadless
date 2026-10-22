import * as THREE from "three";
import { afterEach, describe, expect, it } from "vitest";

import {
  firstDrawnHit,
  firstDrawnHitOn,
  followPartView,
  restorePart,
  showPart,
  showsModelAsItIs,
} from "./partView";
import { viewportStore } from "./viewportStore";

const BLUE = new THREE.Color(0x0000ff);

function part(color = 0x808080): { group: THREE.Group; material: THREE.MeshStandardMaterial } {
  const material = new THREE.MeshStandardMaterial({ color, emissive: 0x111111 });
  const group = new THREE.Group().add(new THREE.Mesh(new THREE.BoxGeometry(), material));
  return { group, material };
}

afterEach(() => viewportStore.resetParts());

describe("showPart", () => {
  it("hides a part the list hid and shows the rest", () => {
    const a = part();
    const b = part();
    showPart(a.group, 0, [0], null, BLUE);
    showPart(b.group, 1, [0], null, BLUE);
    expect(a.group.visible).toBe(false);
    expect(b.group.visible).toBe(true);
  });

  it("lights the part picked out, and puts it back exactly as it was", () => {
    const a = part();
    showPart(a.group, 0, [], 0, BLUE);
    expect(a.material.emissive.getHex()).toBe(0x0000ff);

    showPart(a.group, 0, [], null, BLUE);
    expect(a.material.emissive.getHex()).toBe(0x111111);
    expect(a.material.emissiveIntensity).toBe(1);
  });

  it("puts a part back as it was loaded when it goes away", () => {
    const a = part();
    showPart(a.group, 0, [0], 0, BLUE);
    restorePart(a.group);
    expect(a.group.visible).toBe(true);
    expect(a.material.emissive.getHex()).toBe(0x111111);
  });

  it("leaves a material that cannot glow as it is", () => {
    const material = new THREE.MeshBasicMaterial();
    const group = new THREE.Group().add(new THREE.Mesh(new THREE.BoxGeometry(), material));
    expect(() => showPart(group, 0, [], 0, BLUE)).not.toThrow();
  });
});

describe("followPartView", () => {
  it("applies what the store says as it changes, and stops when told", () => {
    const a = part();
    const b = part();
    const groups = new Map<number, THREE.Object3D>([
      [0, a.group],
      [1, b.group],
    ]);
    const stop = followPartView(groups, () => BLUE);

    viewportStore.togglePart(1);
    expect(b.group.visible).toBe(false);
    viewportStore.highlightPart(0);
    expect(a.material.emissive.getHex()).toBe(0x0000ff);

    stop();
    viewportStore.togglePart(1);
    expect(b.group.visible).toBe(false);
  });
});

describe("firstDrawnHit", () => {
  it("skips a hit on a part that is hidden", () => {
    const hidden = part();
    const shown = part();
    hidden.group.visible = false;
    const hits = [
      { object: hidden.group.children[0], distance: 1 },
      { object: shown.group.children[0], distance: 2 },
    ];
    expect(firstDrawnHit(hits)?.distance).toBe(2);
    expect(firstDrawnHit(hits.slice(0, 1))).toBeUndefined();
  });
});

describe("firstDrawnHitOn", () => {
  it("takes the point from the model's own hits, not from something drawn over it", () => {
    const model = new THREE.Group();
    const cube = new THREE.Group();
    const shown = part();
    model.add(shown.group);
    const hits = [
      { object: new THREE.Mesh(), eventObject: cube, distance: 0 },
      { object: shown.group.children[0], eventObject: model, distance: 5 },
    ];
    expect(firstDrawnHitOn(hits, model)?.distance).toBe(5);
  });
});

describe("showsModelAsItIs", () => {
  it("is true only with no part hidden and none picked out", () => {
    expect(showsModelAsItIs({ hiddenParts: [], highlightedPart: null })).toBe(true);
    expect(showsModelAsItIs({ hiddenParts: [1], highlightedPart: null })).toBe(false);
    expect(showsModelAsItIs({ hiddenParts: [], highlightedPart: 0 })).toBe(false);
  });
});
