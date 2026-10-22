/** A whole-assembly GLB as parts the viewport can move, hide and pick out.
 *
 * A build of several parts is written as one file as well as one per part, and
 * the one file carries every part's name and colour in a single request. Drawn
 * as it loads, though, it is one object, while everything that works on a part
 * — the assembly player, hiding, highlighting — finds it by ordinal in a group
 * of its own. This turns the file back into those parts.
 *
 * A part is a node that carries a mesh, and the file lists them in part order.
 * The file's groups and its Z-up-to-Y-up root are folded into each part's own
 * transform, so a part sits in the frame a per-part file is drawn in and moves
 * along the same headings. Kept apart from the Canvas so it can be tested on a
 * parsed file. */
import * as THREE from "three";

import { weldModel } from "./weld";

/** What is read of a loaded file. Spelled out rather than taken from a loader's
 * own type: the viewport's loader and the tests' come from different builds of
 * three's loaders, whose types do not agree, and this is all either is asked. */
interface Loaded {
  scene: THREE.Object3D;
  parser: {
    json: unknown;
    associations: { get(object: THREE.Object3D): { nodes?: number } | undefined };
  };
}

/** The file's part nodes in part order: every node that carries a mesh, depth first. */
export function partNodes(gltf: Loaded): THREE.Object3D[] {
  const nodes = (gltf.parser.json as { nodes?: { mesh?: number }[] }).nodes ?? [];
  const found: THREE.Object3D[] = [];
  gltf.scene.traverse((object) => {
    const node = gltf.parser.associations.get(object)?.nodes;
    if (node !== undefined && nodes[node]?.mesh !== undefined) found.push(object);
  });
  return found;
}

/** Each part node lifted out of the file into the frame of its scene, and welded.
 *
 * Welded one part at a time rather than as a whole file: welding merges sibling
 * meshes that share a material, and two parts of one colour side by side in the
 * file are exactly that. Null when a part holds another, which part order has
 * no way to describe. */
function lift(gltf: Loaded, nodes: THREE.Object3D[]): THREE.Object3D[] | null {
  const isPart = new Set(nodes);
  for (const node of nodes) {
    let holdsPart = false;
    node.traverse((o) => {
      if (o !== node && isPart.has(o)) holdsPart = true;
    });
    if (holdsPart) return null;
  }
  gltf.scene.updateMatrixWorld(true);
  const toScene = gltf.scene.matrixWorld.clone().invert();
  return nodes.map((node) => {
    const part = node.clone(true);
    // The matrix as it is rather than taken apart into a position, a rotation
    // and a scale, which a chain of transforms cannot always be.
    part.matrix.multiplyMatrices(toScene, node.matrixWorld);
    part.matrixAutoUpdate = false;
    weldModel(part);
    return part;
  });
}

/** Whether `part` carries the name `name`.
 *
 * Read from the name the loader keeps as the file spells it, beside the one it
 * makes safe for animation tracks. A file may spell a name outside ASCII as its
 * UTF-8 bytes read one to a character — the engine's files did, while they came
 * through a writer that reads a name that way — and a file once written stays
 * written, so that spelling is the same name. */
function spells(part: THREE.Object3D, name: string): boolean {
  const spelled = (part.userData.name as string | undefined) ?? part.name;
  if (spelled === name) return true;
  const bytewise = Array.from(new TextEncoder().encode(name), (b) => String.fromCharCode(b)).join("");
  return spelled === bytewise;
}

/** Lifted once per loaded file. The loader keeps a file by address and hands
 * the same one back, so a model drawn again is not lifted and welded again. The
 * loaded file keeps its own unwelded geometry beside these for as long as the
 * loader keeps the file — the price of not altering what the loader hands to
 * every caller. A refusal is not kept: it answers one count and one set of
 * names, and is found again before any welding. */
const lifted = new WeakMap<THREE.Object3D, THREE.Object3D[]>();

export interface OwnedParts {
  /** The parts in part order, each this mount's own to move, hide and light. */
  objects: THREE.Object3D[];
  /** Let go of the materials made for this mount. */
  dispose: () => void;
}

/** A whole-assembly file's parts for one mount, or null when it does not hold
 * `count` separate parts, or holds them under other names than `names`.
 *
 * A count that does not match is refused rather than drawn: the parts are
 * found by position in the file, and one part short would put every later part
 * under the wrong ordinal, so the player would move the wrong piece. So is a
 * file whose parts are named otherwise than the version names them, where it
 * names them (`names[i]` null asks nothing of part `i`): the same count in
 * another order would put every name, joint and highlight on the wrong piece.
 *
 * Each part is a copy, and so is each of its materials. The loaded file is
 * shared by every mount of it, so moving or hiding its own nodes would do it
 * everywhere it is drawn; and parts of one colour share one material in the
 * file, so lighting one part's material would light every part that colour.
 *
 * Both checks read the file's own nodes, so a file refused for either costs
 * nothing to refuse: nothing is copied or welded first. */
export function assemblyParts(
  gltf: Loaded,
  count: number,
  names: readonly (string | null)[] = [],
): OwnedParts | null {
  const nodes = partNodes(gltf);
  if (nodes.length !== count) return null;
  if (names.some((name, i) => name !== null && !spells(nodes[i], name))) return null;
  const template = lifted.get(gltf.scene) ?? lift(gltf, nodes);
  if (!template) return null;
  lifted.set(gltf.scene, template);

  const made: THREE.Material[] = [];
  const objects = template.map((part) => {
    const own = part.clone(true);
    const copies = new Map<THREE.Material, THREE.Material>();
    const copy = (material: THREE.Material) => {
      let mine = copies.get(material);
      if (!mine) {
        mine = material.clone();
        copies.set(material, mine);
        made.push(mine);
      }
      return mine;
    };
    own.traverse((o) => {
      const mesh = o as THREE.Mesh;
      if (!mesh.isMesh) return;
      mesh.material = Array.isArray(mesh.material) ? mesh.material.map(copy) : copy(mesh.material);
    });
    return own;
  });
  return { objects, dispose: () => made.forEach((m) => m.dispose()) };
}
