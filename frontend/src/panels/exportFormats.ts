/** Export format metadata + helpers. Pure / unit-tested. */
import type { ArtifactKind, Version } from "../api";

export const FORMAT_META: Record<ArtifactKind, { label: string; desc: string }> = {
  step: { label: "STEP", desc: "B-Rep — engineering / CAD interchange" },
  stl: { label: "STL", desc: "Mesh — 3D printing" },
  obj: { label: "OBJ", desc: "Mesh — generic interchange" },
  glb: { label: "GLB", desc: "glTF — web / AR viewers" },
};

const ORDER: ArtifactKind[] = ["step", "stl", "obj", "glb"];

/** The artifact kinds actually present on a version, in a stable display order. */
export function availableFormats(version: Version): ArtifactKind[] {
  const present = new Set(version.artifacts.map((a) => a.kind));
  return ORDER.filter((k) => present.has(k));
}

/** Every file of one kind a version holds, in part order.
 *
 * Sorted here rather than trusted from the server: the order decides which name
 * each file is saved under, and a piece saved under another piece's name is a
 * mistake nobody can see afterwards — every file is present and one of them is
 * the wrong shape.
 */
export function partsOf(version: Version, kind: ArtifactKind): Version["artifacts"] {
  return version.artifacts.filter((a) => a.kind === kind).sort((a, b) => a.part - b.part);
}

/** What a format chip reads.
 *
 * A model in several pieces says so, because one click then saves several files
 * and a reader who was not told sees a burst of downloads they did not ask for.
 */
export function chipLabel(version: Version, kind: ArtifactKind): string {
  const count = partsOf(version, kind).length;
  return count > 1 ? `${FORMAT_META[kind].label} · ${count} pieces` : FORMAT_META[kind].label;
}

/** What to save an artifact as. Naming a part gives each piece its own name;
 * without one, the name a whole model has always been saved under. */
export function downloadFilename(versionId: number, kind: ArtifactKind, part?: number): string {
  return part === undefined ? `model_${versionId}.${kind}` : `model_${versionId}_p${part}.${kind}`;
}

/** Share URL for a specific version of a project: the project is in the path
 * (`<base><id>`, deep-linkable) and the version is pinned with `?v=`. */
export function shareUrl(origin: string, base: string, projectId: number, versionId: number): string {
  const path = base.endsWith("/") ? `${base}${projectId}` : `${base}/${projectId}`;
  const url = new URL(path, origin);
  url.searchParams.set("v", String(versionId));
  return url.toString();
}
