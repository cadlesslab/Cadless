/** mm-scale multi-body GLB regression coverage (#43).
 *
 * Houses prove the multi-body path only at metre scale: authored in m, baked
 * ×1000 to mm, then divided back to metres at glTF export — they arrive in the
 * viewport ~10 units across. mm-scale mechanical Compounds (piston assembly,
 * butt hinge) instead arrive ~0.05–0.1 units across, as one glTF node per
 * solid with one primitive per B-Rep face and an OCCT Z-up→Y-up root rotation.
 *
 * True WebGL rendering is not available under jsdom, so — like weld.test.ts —
 * these tests cover the load/scene-graph/bounds layer: GLB parsing yields
 * every body, the union bounding box (what drei's <Bounds> frames) spans all
 * bodies, framing math stays finite at mm scale, and weldModel merges facet
 * primitives within a body without merging bodies or moving geometry.
 */
import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { describe, expect, it } from "vitest";

import { frameDistance } from "./math";
import { weldModel } from "./weld";

// --------------------------------------------------------------------------- //
// minimal binary-glTF writer (mirrors the OCCT exporter's document shape)
// --------------------------------------------------------------------------- //

interface BodyDef {
  name: string;
  translation?: [number, number, number];
  primitives: THREE.BufferGeometry[];
}

/** Encode bodies as a GLB: root rotation node, one node+mesh per body, one
 *  primitive per geometry, no materials (OCCT emits none). */
function encodeGlb(bodies: BodyDef[]): ArrayBuffer {
  const bufferViews: object[] = [];
  const accessors: object[] = [];
  const meshes: object[] = [];
  const rootChildren: number[] = [];
  const nodes: object[] = [
    // OCCT-style root: rotate the Z-up model frame into glTF's Y-up.
    { name: "root", rotation: [-Math.SQRT1_2, 0, 0, Math.SQRT1_2], children: rootChildren },
  ];
  const chunks: Uint8Array[] = [];
  let byteOffset = 0;

  const pushView = (bytes: Uint8Array): number => {
    bufferViews.push({ buffer: 0, byteOffset, byteLength: bytes.byteLength });
    chunks.push(bytes);
    const pad = (4 - (bytes.byteLength % 4)) % 4;
    if (pad) chunks.push(new Uint8Array(pad));
    byteOffset += bytes.byteLength + pad;
    return bufferViews.length - 1;
  };

  const pushVec3 = (arr: Float32Array): number => {
    const min = [Infinity, Infinity, Infinity];
    const max = [-Infinity, -Infinity, -Infinity];
    for (let i = 0; i < arr.length; i += 3) {
      for (let c = 0; c < 3; c++) {
        min[c] = Math.min(min[c], arr[i + c]);
        max[c] = Math.max(max[c], arr[i + c]);
      }
    }
    accessors.push({
      bufferView: pushView(new Uint8Array(arr.buffer, arr.byteOffset, arr.byteLength)),
      componentType: 5126, // FLOAT
      count: arr.length / 3,
      type: "VEC3",
      min,
      max,
    });
    return accessors.length - 1;
  };

  for (const body of bodies) {
    const primitives = body.primitives.map((g) => {
      const position = pushVec3(new Float32Array(g.getAttribute("position").array));
      const normal = pushVec3(new Float32Array(g.getAttribute("normal").array));
      const idx = new Uint16Array(g.index!.array);
      accessors.push({
        bufferView: pushView(new Uint8Array(idx.buffer, idx.byteOffset, idx.byteLength)),
        componentType: 5123, // UNSIGNED_SHORT
        count: idx.length,
        type: "SCALAR",
      });
      return { attributes: { POSITION: position, NORMAL: normal }, indices: accessors.length - 1 };
    });
    meshes.push({ primitives });
    nodes.push({
      name: body.name,
      mesh: meshes.length - 1,
      ...(body.translation ? { translation: body.translation } : {}),
    });
    rootChildren.push(nodes.length - 1);
  }

  const json = {
    asset: { version: "2.0" },
    scene: 0,
    scenes: [{ nodes: [0] }],
    nodes,
    meshes,
    accessors,
    bufferViews,
    buffers: [{ byteLength: byteOffset }],
  };
  let jsonBytes = new TextEncoder().encode(JSON.stringify(json));
  const jsonPad = (4 - (jsonBytes.length % 4)) % 4;
  if (jsonPad) {
    const padded = new Uint8Array(jsonBytes.length + jsonPad).fill(0x20); // spaces
    padded.set(jsonBytes);
    jsonBytes = padded;
  }

  const total = 12 + 8 + jsonBytes.length + 8 + byteOffset;
  const out = new ArrayBuffer(total);
  const dv = new DataView(out);
  const u8 = new Uint8Array(out);
  dv.setUint32(0, 0x46546c67, true); // "glTF"
  dv.setUint32(4, 2, true);
  dv.setUint32(8, total, true);
  dv.setUint32(12, jsonBytes.length, true);
  dv.setUint32(16, 0x4e4f534a, true); // "JSON"
  u8.set(jsonBytes, 20);
  let off = 20 + jsonBytes.length;
  dv.setUint32(off, byteOffset, true);
  dv.setUint32(off + 4, 0x004e4942, true); // "BIN"
  off += 8;
  for (const c of chunks) {
    u8.set(c, off);
    off += c.byteLength;
  }
  return out;
}

// --------------------------------------------------------------------------- //
// fixture: a two-body mm-scale assembly (coordinates in metres, glTF style)
// --------------------------------------------------------------------------- //

/** Indexed quad in the XY plane at z=0 with +Z normals (weld.test.ts shape). */
function quad(x0: number, y0: number, w: number, h: number): THREE.BufferGeometry {
  const g = new THREE.BufferGeometry();
  // prettier-ignore
  const pos = new Float32Array([
    x0, y0, 0,  x0 + w, y0, 0,  x0 + w, y0 + h, 0,  x0, y0 + h, 0,
  ]);
  const nrm = new Float32Array([0, 0, 1, 0, 0, 1, 0, 0, 1, 0, 0, 1]);
  g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  g.setAttribute("normal", new THREE.BufferAttribute(nrm, 3));
  g.setIndex([0, 1, 2, 0, 2, 3]);
  return g;
}

// Crown: an 85.6 × 42.8 mm plate split into two face primitives sharing the
// x=0 seam (the OCCT one-primitive-per-face export shape). Pin: a
// 20 × 62 × 20 mm box on its own node, offset 18 mm below the crown.
const CROWN = 0.0428; // metres
const PIN_TRANSLATION: [number, number, number] = [0, 0, -0.018];

function makeAssemblyGlb(): ArrayBuffer {
  return encodeGlb([
    {
      name: "piston-crown",
      primitives: [quad(-CROWN, -CROWN / 2, CROWN, CROWN), quad(0, -CROWN / 2, CROWN, CROWN)],
    },
    {
      name: "wrist-pin",
      translation: PIN_TRANSLATION,
      primitives: [new THREE.BoxGeometry(0.02, 0.062, 0.02)],
    },
  ]);
}

function loadGlb(buffer: ArrayBuffer): Promise<THREE.Group> {
  return new Promise((resolve, reject) => {
    new GLTFLoader().parse(buffer, "", (gltf) => resolve(gltf.scene), reject);
  });
}

function meshesIn(root: THREE.Object3D): THREE.Mesh[] {
  const found: THREE.Mesh[] = [];
  root.traverse((o) => {
    if ((o as THREE.Mesh).isMesh) found.push(o as THREE.Mesh);
  });
  return found;
}

function triangleCount(root: THREE.Object3D): number {
  let n = 0;
  for (const m of meshesIn(root)) {
    const g = m.geometry;
    n += (g.index ? g.index.count : g.getAttribute("position").count) / 3;
  }
  return n;
}

// --------------------------------------------------------------------------- //
// tests
// --------------------------------------------------------------------------- //

describe("mm-scale multi-body GLB (#43)", () => {
  it("loads every body of the assembly", async () => {
    const scene = await loadGlb(makeAssemblyGlb());

    // one primitive per face → 2 crown meshes + 1 pin mesh
    expect(meshesIn(scene)).toHaveLength(3);
    expect(triangleCount(scene)).toBe(16); // 2 quads (4) + box (12)

    const crown = scene.getObjectByName("piston-crown");
    const pin = scene.getObjectByName("wrist-pin");
    expect(crown).toBeDefined();
    expect(pin).toBeDefined();
    expect(meshesIn(crown!)).toHaveLength(2);
    expect(meshesIn(pin!)).toHaveLength(1);
  });

  it("union bounds span all bodies at mm scale (what <Bounds> frames)", async () => {
    const scene = await loadGlb(makeAssemblyGlb());
    const box = new THREE.Box3().setFromObject(scene);

    for (const v of [box.min, box.max]) {
      expect(Number.isFinite(v.x) && Number.isFinite(v.y) && Number.isFinite(v.z)).toBe(true);
    }

    // Model space is Z-up; the root rotation maps (x, y, z) → (x, z, -y).
    // Crown alone: x ±0.0428, y = 0, z ±0.0214.
    expect(box.min.x).toBeCloseTo(-CROWN, 6);
    expect(box.max.x).toBeCloseTo(CROWN, 6);
    // The pin extends the union beyond the flat crown in both other axes —
    // this is exactly what a fit against a single body would miss.
    expect(box.min.y).toBeCloseTo(-0.028, 6); // pin bottom (translation z − 10 mm)
    expect(box.max.z).toBeCloseTo(0.031, 6); // pin half-length (31 mm)
    expect(box.min.z).toBeCloseTo(-0.031, 6);

    // mm scale sanity: the whole assembly is centimetres across, not metres.
    const size = box.getSize(new THREE.Vector3());
    const maxDim = Math.max(size.x, size.y, size.z);
    expect(maxDim).toBeCloseTo(2 * CROWN, 6);
    expect(maxDim).toBeGreaterThan(0.01);
    expect(maxDim).toBeLessThan(1);
  });

  it("framing math stays finite and sane at mm scale", async () => {
    const scene = await loadGlb(makeAssemblyGlb());
    const box = new THREE.Box3().setFromObject(scene);
    const size = box.getSize(new THREE.Vector3());
    const dist = frameDistance(Math.max(size.x, size.y, size.z), 50);

    expect(Number.isFinite(dist)).toBe(true);
    expect(dist).toBeGreaterThan(0);
    expect(dist).toBeLessThan(1); // metres — the camera stays near a mm part
  });

  it("weldModel merges facet primitives per body without merging bodies", async () => {
    const scene = await loadGlb(makeAssemblyGlb());
    const before = new THREE.Box3().setFromObject(scene);

    weldModel(scene);

    // crown facets merged into one mesh, seam verts welded (8 → 6); the pin
    // stays its own mesh — bodies are never collapsed together.
    const crownMeshes = meshesIn(scene.getObjectByName("piston-crown")!);
    expect(crownMeshes).toHaveLength(1);
    expect(crownMeshes[0].geometry.getAttribute("position").count).toBe(6);
    expect(meshesIn(scene)).toHaveLength(2);
    expect(triangleCount(scene)).toBe(16);

    // welding at the 1e-4 default tolerance must not move mm-scale geometry
    const after = new THREE.Box3().setFromObject(scene);
    for (const axis of ["x", "y", "z"] as const) {
      expect(after.min[axis]).toBeCloseTo(before.min[axis], 9);
      expect(after.max[axis]).toBeCloseTo(before.max[axis], 9);
    }
  });
});
