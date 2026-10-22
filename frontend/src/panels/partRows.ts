/** What the part list shows for each part of a version. Pure / unit-tested. */
import * as THREE from "three";

import type { Version } from "../api";
import { partsOf } from "./exportFormats";

export interface PartRow {
  /** The ordinal the part's files are fetched by, and the viewport moves it by. */
  part: number;
  /** What the reader is shown: the name its build gave it, or its number. */
  name: string;
  /** The swatch colour as CSS, or null where the build gave the part none. */
  color: string | null;
}

/** The name a version's build gave part `part`, on one line, or null where it
 * gave none.
 *
 * `part<i>` is what the engine calls a part its build did not name, so it is
 * no name: read as one, a list would call the part by its zero-based number
 * where everything else counts from one. */
export function givenName(version: Version, part: number): string | null {
  const name = (version.parts?.find((p) => p.index === part)?.name ?? "")
    .split(/\s+/)
    .filter(Boolean)
    .join(" ");
  return name && name !== `part${part}` ? name : null;
}

/** What each of the parts `ordinals` names is called, in the same order.
 *
 * A part is called by its build's name, or `part N` counted from one where it
 * has none. Where two parts would read alike — two that share a name, or a name
 * that is another part's number — each named one has its number beside its
 * name, so two parts that share a name, or a name and a number, are told
 * apart. */
export function partLabels(version: Version, ordinals: readonly number[]): string[] {
  const given = ordinals.map((part) => givenName(version, part));
  const plain = ordinals.map((part, i) => given[i] ?? `part ${part + 1}`);
  const seen = new Map<string, number>();
  for (const label of plain) seen.set(label, (seen.get(label) ?? 0) + 1);
  return plain.map((label, i) =>
    seen.get(label)! > 1 && given[i] !== null ? `${label} (part ${ordinals[i] + 1})` : label,
  );
}

/** One row per part of a version drawn in parts, in part order; none for a
 * version with one part. */
export function partRows(version: Version): PartRow[] {
  const ordinals = partsOf(version, "glb").map((a) => a.part);
  if (ordinals.length < 2) return [];
  const labels = partLabels(version, ordinals);
  return ordinals.map((part, i) => {
    const color = version.parts?.find((p) => p.index === part)?.color ?? null;
    return { part, name: labels[i], color: color ? swatch(color) : null };
  });
}

/** The colour the build gave a part, as the viewport draws a coloured part, or
 * null for one that is not four numbers.
 *
 * The colour is held as linear values and the viewport shows them converted to
 * sRGB, so the swatch is converted the same way: a mid grey written as 0.5 would
 * otherwise read darker in the list than on the model. Checked rather than
 * trusted, because the result is written into a CSS value, where anything but a
 * number would be read as CSS. */
function swatch(color: readonly unknown[]): string | null {
  if (color.length !== 4 || !color.every((v) => typeof v === "number" && Number.isFinite(v))) {
    return null;
  }
  const [r, g, b, a] = color as number[];
  const c = new THREE.Color()
    .setRGB(r, g, b, THREE.LinearSRGBColorSpace)
    .getRGB({ r: 0, g: 0, b: 0 }, THREE.SRGBColorSpace);
  const alpha = Math.min(Math.max(a, 0), 1);
  return `rgba(${Math.round(c.r * 255)}, ${Math.round(c.g * 255)}, ${Math.round(c.b * 255)}, ${alpha})`;
}
