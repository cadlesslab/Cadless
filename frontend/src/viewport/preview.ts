/** What the viewport is showing, decided in one place.
 *
 * The viewport used to work this out inline, and each answer sat next to the
 * thing that consumed it: the URL beside the loader, the thumbnail capture
 * beside the model, the empty-state copy at the bottom. That was fine while
 * there was only ever one thing it could be showing — the active version.
 *
 * A catalogue item can now be looked at before it is fetched, and then
 * what is on screen and what the rest of the app calls "active" are two
 * different objects. Answers that used to agree by construction have to be
 * decided together, so they are decided here, where they can be tested without
 * a Canvas. */
import { glbUrl, type Version } from "../api";
import type { Preview } from "./viewportStore";

const NOTHING_OPEN = "Generate a part to see it here.";
const GENERATION_FAILED = "Generation failed — fix the prompt and try again.";
const MODEL_UNREADABLE = "This model could not be loaded.";
const PREVIEW_UNREADABLE = "That catalogue's preview could not be loaded.";
const PREVIEW_HAS_NO_MESH = "This catalogue has no model to preview.";

export interface ViewerSubject {
  /** The GLB to draw, or null when there is nothing to draw. */
  url: string | null;
  /** Whether what is on screen belongs to something not on this machine. */
  previewing: boolean;
  /** The version to save a thumbnail of, or null to save none. */
  captureVersionId: number | null;
  /** Why the viewport is empty, when it is. Null while something is drawn. */
  emptyReason: string | null;
}

/** What to draw, whether to keep a picture of it, and what to say instead.
 *
 * `failed` is the loader's answer rather than this function's: a GLB that a
 * server answered for can still be unreadable, and only the attempt knows. */
export function viewerSubject(
  version: Version | null,
  preview: Preview | null,
  failed: boolean,
): ViewerSubject {
  if (preview) {
    // captureVersionId is null on every path through here, whether or not a
    // project is open behind the preview. Thumbnails are kept under a version
    // id, so capturing while a previewed mesh is on screen would file that
    // render as the active version's picture — in the version list, in the
    // transcript, and for as long as the entry survives.
    if (preview.url == null) {
      return {
        url: null,
        previewing: true,
        captureVersionId: null,
        emptyReason: preview.note ?? PREVIEW_HAS_NO_MESH,
      };
    }
    if (failed) {
      return {
        url: null,
        previewing: true,
        captureVersionId: null,
        emptyReason: PREVIEW_UNREADABLE,
      };
    }
    return { url: preview.url, previewing: true, captureVersionId: null, emptyReason: null };
  }

  const hasModel = version?.ok === true && version.artifacts.some((a) => a.kind === "glb");
  if (hasModel && !failed) {
    return {
      url: glbUrl(version.id),
      previewing: false,
      captureVersionId: version.id,
      emptyReason: null,
    };
  }
  // A model that was named and would not load is its own answer, and a
  // different one from never having generated anything.
  const emptyReason = hasModel
    ? MODEL_UNREADABLE
    : version && !version.ok
      ? GENERATION_FAILED
      : NOTHING_OPEN;
  return { url: null, previewing: false, captureVersionId: null, emptyReason };
}

export interface ReadoutFields {
  /** Whether the readout has anything worth showing. */
  shown: boolean;
  bbox: [number, number, number] | null;
  volume: number | null;
  triangles: number | null;
}

/** The measurements that describe what is on screen, and only those.
 *
 * The triangle count is taken off the mesh the viewport loaded; the bounding
 * box and the volume are recorded on the version. Those are the same object
 * right up until a preview is showing, and then printing them together reads
 * as one measurement of one thing when it is two of two. */
export function readoutFor(
  version: Version | null,
  triangles: number | null,
  preview: Preview | null,
): ReadoutFields {
  if (preview) {
    // A preview with nothing to draw has nothing to measure either. The count
    // is cleared when the mesh that was counted goes away, but that happens
    // after the paint — so without asking here, the last catalogue's total is
    // printed for a frame over an empty viewport explaining there is no mesh.
    const drawn = preview.url != null;
    return {
      shown: drawn && triangles != null,
      bbox: null,
      volume: null,
      triangles: drawn ? triangles : null,
    };
  }
  if (!version?.ok) return { shown: false, bbox: null, volume: null, triangles: null };
  // A version can be fine and still have nothing recorded to say about it —
  // both measurements are nullable, and the count is null until the mesh is
  // loaded. Shown regardless, that is an empty pill: a border and a blur with
  // no reading in it.
  const anything = version.bbox != null || version.volume != null || triangles != null;
  return { shown: anything, bbox: version.bbox, volume: version.volume, triangles };
}
