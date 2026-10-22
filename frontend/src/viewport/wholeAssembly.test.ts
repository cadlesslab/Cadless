/** The whole-assembly file turned back into parts, on files parsed with
 * three's own GLTFLoader. jsdom draws nothing, so this covers the scene graph:
 * which node is which part, where each part sits, and what each part owns. */
import * as THREE from "three";
import { describe, expect, it, vi } from "vitest";

import { encodeGlb, type GlbNode, parseGlb } from "../test/glb";
import { weldModel } from "./weld";
import { assemblyParts, partNodes } from "./wholeAssembly";

// The real weld, watched: a refused file should not have paid for it.
vi.mock("./weld", async (original) => {
  const real = await original<typeof import("./weld")>();
  return { ...real, weldModel: vi.fn(real.weldModel) };
});

const RED: [number, number, number, number] = [1, 0, 0, 1];

/** A box of `size` centred at `at`, as one face primitive or several. */
function box(at: [number, number, number], size = 2, faces = 2): THREE.BufferGeometry[] {
  return Array.from({ length: faces }, () => {
    const g = new THREE.BoxGeometry(size, size, size).toNonIndexed();
    g.setIndex(Array.from({ length: g.getAttribute("position").count }, (_, i) => i));
    g.translate(...at);
    return g;
  });
}

/** A toy car in the file's shape: a chassis, a body group of two parts, and
 * two wheels of one colour, one of them a single primitive. */
const CAR: GlbNode[] = [
  { name: "chassis", primitives: box([0, 0, 0], 4) },
  {
    name: "body",
    translation: [0, 0, 3],
    children: [
      { name: "shell", primitives: box([0, 0, 0], 3) },
      { name: "seat", translation: [1, 0, 0], primitives: box([0, 0, 0], 1) },
    ],
  },
  { name: "wheel_l", color: RED, primitives: box([0, 3, -2], 1, 1) },
  { name: "wheel_r", color: RED, primitives: box([0, -3, -2], 1, 1) },
];

function worldBox(object: THREE.Object3D): THREE.Box3 {
  object.updateWorldMatrix(true, true);
  return new THREE.Box3().setFromObject(object);
}

function expectSameBox(a: THREE.Box3, b: THREE.Box3) {
  for (const corner of ["min", "max"] as const)
    for (const axis of ["x", "y", "z"] as const) expect(a[corner][axis]).toBeCloseTo(b[corner][axis], 6);
}

describe("partNodes", () => {
  it("finds the parts in the order the file lists them, through groups", async () => {
    const gltf = await parseGlb(encodeGlb(CAR));
    expect(partNodes(gltf).map((o) => o.name)).toEqual(["chassis", "shell", "seat", "wheel_l", "wheel_r"]);
  });
});

describe("assemblyParts", () => {
  it("puts every part where the file draws it", async () => {
    const gltf = await parseGlb(encodeGlb(CAR));
    const nodes = partNodes(gltf);
    const owned = assemblyParts(gltf, 5)!;

    const model = new THREE.Group();
    owned.objects.forEach((o) => model.add(new THREE.Group().add(o)));
    owned.objects.forEach((o, i) => expectSameBox(worldBox(o), worldBox(nodes[i])));
  });

  it("sits where the same part drawn from its own file sits", async () => {
    // The player moves a part by an offset on the group it is drawn in, the
    // same for either kind of file, so a lifted part that sits where its own
    // file puts it is moved to the same place.
    const whole = await parseGlb(encodeGlb(CAR));
    const lifted = new THREE.Group().add(assemblyParts(whole, 5)!.objects[2]);
    // The seat's own file holds it already placed, as a part file does.
    const alone = await parseGlb(encodeGlb([{ name: "seat", primitives: box([1, 0, 3], 1) }]));
    const drawn = new THREE.Group().add(alone.scene);

    expectSameBox(worldBox(lifted), worldBox(drawn));
  });

  it("keeps two parts of one colour apart, even when each is a single mesh", async () => {
    // Welding merges sibling meshes that share a material; the two wheels are
    // siblings in the file and share its one red material.
    const gltf = await parseGlb(encodeGlb(CAR));
    const owned = assemblyParts(gltf, 5)!;
    const meshes = (o: THREE.Object3D) => {
      const found: THREE.Mesh[] = [];
      o.traverse((m) => (m as THREE.Mesh).isMesh && found.push(m as THREE.Mesh));
      return found;
    };
    expect(meshes(owned.objects[3])).toHaveLength(1);
    expect(meshes(owned.objects[4])).toHaveLength(1);
    // Model +y is scene -z: the left wheel is on the far side, the right near.
    expect(worldBox(owned.objects[3]).max.z).toBeLessThan(0);
    expect(worldBox(owned.objects[4]).min.z).toBeGreaterThan(0);
  });

  it("gives each part materials of its own", async () => {
    const gltf = await parseGlb(encodeGlb(CAR));
    const owned = assemblyParts(gltf, 5)!;
    const material = (o: THREE.Object3D) => {
      let found: THREE.Material | null = null;
      o.traverse((m) => {
        if ((m as THREE.Mesh).isMesh) found = (m as THREE.Mesh).material as THREE.Material;
      });
      return found!;
    };
    const left = material(owned.objects[3]) as THREE.MeshStandardMaterial;
    const right = material(owned.objects[4]) as THREE.MeshStandardMaterial;
    expect(left).not.toBe(right);
    expect(left.color.getHex()).toBe(0xff0000);

    left.emissive.setHex(0x00ff00);
    expect(right.emissive.getHex()).toBe(0x000000);
  });

  it("gives each mount parts of its own", async () => {
    const gltf = await parseGlb(encodeGlb(CAR));
    const first = assemblyParts(gltf, 5)!;
    const second = assemblyParts(gltf, 5)!;
    expect(first.objects[0]).not.toBe(second.objects[0]);
    first.objects[0].visible = false;
    expect(second.objects[0].visible).toBe(true);
  });

  it("refuses a file that does not hold the parts it is asked for", async () => {
    expect(assemblyParts(await parseGlb(encodeGlb(CAR)), 4)).toBeNull();
    expect(assemblyParts(await parseGlb(encodeGlb(CAR)), 6)).toBeNull();
  });

  it("refuses a file of another count before welding any of it", async () => {
    const gltf = await parseGlb(encodeGlb(CAR));
    vi.mocked(weldModel).mockClear();
    expect(assemblyParts(gltf, 4)).toBeNull();
    expect(weldModel).not.toHaveBeenCalled();
    // The refusal answered that count only.
    expect(assemblyParts(gltf, 5)).not.toBeNull();
  });

  it("refuses a file of other names before welding any of it", async () => {
    const gltf = await parseGlb(encodeGlb(CAR));
    vi.mocked(weldModel).mockClear();
    expect(assemblyParts(gltf, 5, ["shell", null, null, null, null])).toBeNull();
    expect(weldModel).not.toHaveBeenCalled();
  });

  it("refuses a file whose parts are named otherwise than the version's", async () => {
    const gltf = await parseGlb(encodeGlb(CAR));
    expect(assemblyParts(gltf, 5, ["chassis", "seat", "shell", null, null])).toBeNull();
    expect(assemblyParts(gltf, 5, ["chassis", "shell", "seat", null, null])).not.toBeNull();
  });

  it("reads a name the file spells as its UTF-8 bytes as that name", async () => {
    // How the engine's files spelled a name outside ASCII while they came
    // through build123d's writer: each UTF-8 byte read as a character of its own.
    const name = "\uc55e \ubc14\ud034";
    const bytewise = Array.from(new TextEncoder().encode(name), (b) => String.fromCharCode(b)).join("");
    const gltf = await parseGlb(
      encodeGlb([
        { name: bytewise, primitives: box([0, 0, 0]) },
        { name: "lid", primitives: box([5, 0, 0]) },
      ]),
    );
    expect(assemblyParts(gltf, 2, [name, "lid"])).not.toBeNull();
    expect(assemblyParts(gltf, 2, ["\ub4b7 \ubc14\ud034", "lid"])).toBeNull();
  });

  it("refuses a file in which a part holds another, before welding any of it", async () => {
    const nested: GlbNode[] = [
      { name: "outer", primitives: box([0, 0, 0]), children: [{ name: "inner", primitives: box([5, 0, 0]) }] },
    ];
    const gltf = await parseGlb(encodeGlb(nested));
    vi.mocked(weldModel).mockClear();
    expect(assemblyParts(gltf, 2)).toBeNull();
    expect(weldModel).not.toHaveBeenCalled();
  });

  it("lets go of the materials it made", async () => {
    const gltf = await parseGlb(encodeGlb(CAR));
    const owned = assemblyParts(gltf, 5)!;
    const made = new Set<THREE.Material>();
    owned.objects.forEach((o) =>
      o.traverse((m) => {
        if ((m as THREE.Mesh).isMesh) made.add((m as THREE.Mesh).material as THREE.Material);
      }),
    );
    const disposed = new Set<THREE.Material>();
    made.forEach((m) => m.addEventListener("dispose", () => disposed.add(m)));
    owned.dispose();
    expect(made.size).toBe(5);
    expect(disposed).toEqual(made);
  });
});
