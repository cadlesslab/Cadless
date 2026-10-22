/** A minimal binary-glTF writer for tests, shaped like the engine's exports.
 *
 * Every document it writes has the OCCT exporter's frame: one root node turning
 * the Z-up model into glTF's Y-up, with the given nodes under it. A node with
 * primitives carries a mesh of one primitive per geometry -- one per B-Rep face,
 * as the exporter writes them -- and a node with children is a group. Nodes of
 * one colour share one material, as the exporter writes them, and a node with no
 * colour has no material at all. */
import type * as THREE from "three";
import { type GLTF, GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";

export interface GlbNode {
  name: string;
  translation?: [number, number, number];
  primitives?: THREE.BufferGeometry[];
  color?: [number, number, number, number];
  children?: GlbNode[];
}

export function encodeGlb(roots: GlbNode[]): ArrayBuffer {
  const bufferViews: object[] = [];
  const accessors: object[] = [];
  const meshes: object[] = [];
  const materials: object[] = [];
  const materialOf = new Map<string, number>();
  const rootChildren: number[] = [];
  const nodes: Record<string, unknown>[] = [
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

  const material = (color: [number, number, number, number]): number => {
    const key = color.join(",");
    let index = materialOf.get(key);
    if (index === undefined) {
      materials.push({ pbrMetallicRoughness: { baseColorFactor: color } });
      index = materials.length - 1;
      materialOf.set(key, index);
    }
    return index;
  };

  const addNode = (node: GlbNode): number => {
    const entry: Record<string, unknown> = { name: node.name };
    if (node.translation) entry.translation = node.translation;
    const index = nodes.length;
    nodes.push(entry);
    if (node.primitives) {
      const mat = node.color ? material(node.color) : undefined;
      const primitives = node.primitives.map((g) => {
        const position = pushVec3(new Float32Array(g.getAttribute("position").array));
        const normal = pushVec3(new Float32Array(g.getAttribute("normal").array));
        const idx = new Uint16Array(g.index!.array);
        accessors.push({
          bufferView: pushView(new Uint8Array(idx.buffer, idx.byteOffset, idx.byteLength)),
          componentType: 5123, // UNSIGNED_SHORT
          count: idx.length,
          type: "SCALAR",
        });
        return {
          attributes: { POSITION: position, NORMAL: normal },
          indices: accessors.length - 1,
          ...(mat === undefined ? {} : { material: mat }),
        };
      });
      meshes.push({ primitives });
      entry.mesh = meshes.length - 1;
    }
    if (node.children) entry.children = node.children.map(addNode);
    return index;
  };
  for (const node of roots) rootChildren.push(addNode(node));

  const json = {
    asset: { version: "2.0" },
    scene: 0,
    scenes: [{ nodes: [0] }],
    nodes,
    meshes,
    ...(materials.length ? { materials } : {}),
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

/** Parse a GLB with three's own GLTFLoader. */
export function parseGlb(buffer: ArrayBuffer): Promise<GLTF> {
  return new Promise((resolve, reject) => new GLTFLoader().parse(buffer, "", resolve, reject));
}
