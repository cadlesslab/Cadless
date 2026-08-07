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

export function downloadFilename(versionId: number, kind: ArtifactKind): string {
  return `model_${versionId}.${kind}`;
}

/** Share URL for a specific version of a project: the project is in the path
 * (`<base><id>`, deep-linkable) and the version is pinned with `?v=`. */
export function shareUrl(origin: string, base: string, projectId: number, versionId: number): string {
  const path = base.endsWith("/") ? `${base}${projectId}` : `${base}/${projectId}`;
  const url = new URL(path, origin);
  url.searchParams.set("v", String(versionId));
  return url.toString();
}
