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

/** How many characters of a part's name go into a file name. See
 * `downloadFilename`. */
const NAME_CHARS = 48;

/** What to save an artifact as. Naming a part gives each piece its own name;
 * without one, the name a whole model has always been saved under.
 *
 * A piece its build named carries that name after its number, so a folder of
 * pieces reads as the parts they are. The number stays: two parts may share a
 * name, and a browser saving a second file of one name tells them apart only by
 * a suffix of its own, which says nothing about which part is which. What a
 * file system will not take in a name is replaced, and a name left with nothing
 * in it is dropped. The name is written composed and cut at `NAME_CHARS`
 * characters, which leaves the whole file name inside the 255 bytes a file
 * system takes even for a script written three bytes to a character — as long
 * as it is stored composed, as most are; one that decomposes every name may
 * take fewer. It is cut by characters rather than by UTF-16 units, so a cut
 * never splits one in two and leaves half of it in the file name. */
export function downloadFilename(
  versionId: number,
  kind: ArtifactKind,
  part?: number,
  name?: string | null,
): string {
  if (part === undefined) return `model_${versionId}.${kind}`;
  const slug = Array.from((name ?? "").normalize("NFC").replace(/[\\/:*?"<>|\s]+/g, "_"))
    .slice(0, NAME_CHARS)
    .join("")
    .replace(/^[._]+|[._]+$/g, "");
  return slug ? `model_${versionId}_p${part}_${slug}.${kind}` : `model_${versionId}_p${part}.${kind}`;
}

/** Share URL for a specific version of a project: the project is in the path
 * (`<base><id>`, deep-linkable) and the version is pinned with `?v=`. */
export function shareUrl(origin: string, base: string, projectId: number, versionId: number): string {
  const path = base.endsWith("/") ? `${base}${projectId}` : `${base}/${projectId}`;
  const url = new URL(path, origin);
  url.searchParams.set("v", String(versionId));
  return url.toString();
}
