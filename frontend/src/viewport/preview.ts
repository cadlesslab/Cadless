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
import {
  artifactUrl,
  glbUrl,
  hasWholeAssembly,
  type Version,
  wholeAssemblyUrl,
} from "../api";
import { partsOf } from "../panels/exportFormats";
import type { Preview } from "./viewportStore";

const NOTHING_OPEN = "Generate a part to see it here.";
const GENERATION_FAILED = "Generation failed — fix the prompt and try again.";
const MODEL_UNREADABLE = "This model could not be loaded.";
const PREVIEW_UNREADABLE = "That catalogue's preview could not be loaded.";
const PREVIEW_HAS_NO_MESH = "This catalogue has no model to preview.";

export interface ViewerSubject {
  /** The GLBs to draw, or none when there is nothing to draw: for the active
   * version in parts its whole-assembly file, or one per part in part order;
   * one for a model printed whole; and for a preview its one address. */
  urls: string[];
  /** Whether `urls` is a version's one whole-assembly file, to be drawn as the
   * parts `parts` names rather than as one object. */
  fromAssemblyFile: boolean;
  /** Whether what is on screen belongs to something not on this machine. */
  previewing: boolean;
  /** The version to save a thumbnail of, or null to save none. */
  captureVersionId: number | null;
  /** Why the viewport is empty, when it is. Null while something is drawn. */
  emptyReason: string | null;
  /** The GLB part ordinal each of `urls` draws, in the same order — the number
   * an assembly names that part by — or, for a whole-assembly file, the ordinal
   * of each of its parts in turn. Empty when there are no parts to name: a model
   * printed whole, a preview, or nothing at all. */
  parts: number[];
  /** What the version calls each of `parts`, in the same order, or null for one
   * it gives no name — what a whole-assembly file's parts are checked against,
   * because a file whose parts are in another order is not this model. */
  names: (string | null)[];
}

/** What to draw, whether to keep a picture of it, and what to say instead.
 *
 * `failed` is the loader's answer rather than this function's: a GLB that a
 * server answered for can still be unreadable, and only the attempt knows.
 * `wholeRefused` is the same kind of answer about a whole-assembly file — it
 * would not load, or did not hold the parts — and sends the version back to its
 * per-part files. */
export function viewerSubject(
  version: Version | null,
  preview: Preview | null,
  failed: boolean,
  wholeRefused = false,
): ViewerSubject {
  if (preview) {
    // captureVersionId is null on every path through here, whether or not a
    // project is open behind the preview. Thumbnails are kept under a version
    // id, so capturing while a previewed mesh is on screen would file that
    // render as the active version's picture — in the version list, in the
    // transcript, and for as long as the entry survives.
    if (preview.url == null) {
      return {
        urls: [],
        fromAssemblyFile: false,
        previewing: true,
        captureVersionId: null,
        emptyReason: preview.note ?? PREVIEW_HAS_NO_MESH,
        parts: [],
        names: [],
      };
    }
    if (failed) {
      return {
        urls: [],
        fromAssemblyFile: false,
        previewing: true,
        captureVersionId: null,
        emptyReason: PREVIEW_UNREADABLE,
        parts: [],
        names: [],
      };
    }
    return {
      urls: [preview.url],
      fromAssemblyFile: false,
      previewing: true,
      captureVersionId: null,
      emptyReason: null,
      parts: [],
      names: [],
    };
  }

  const hasModel = version?.ok === true && version.artifacts.some((a) => a.kind === "glb");
  if (hasModel && !failed) {
    return {
      ...modelUrls(version, wholeRefused),
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
  return {
    urls: [],
    fromAssemblyFile: false,
    previewing: false,
    captureVersionId: null,
    emptyReason,
    parts: [],
    names: [],
  };
}

/** Every GLB a version's model is drawn from, and the part each one is.
 *
 * A model printed whole keeps the one address it has always been fetched from.
 * A model in parts is drawn from its whole-assembly file where it has one: one
 * request rather than one per part, and the file carries the colours its
 * build gave the parts, which the part files do not. A version written before
 * there was such a file, or one whose file would not do, is asked for part by
 * part, so each piece is still drawn as itself rather than one of them standing
 * in for the rest. The ordinals travel with the addresses because whoever moves
 * a part by its ordinal needs to know which part that is, not where it fell in
 * a list. */
function modelUrls(
  version: Version,
  wholeRefused: boolean,
): { urls: string[]; parts: number[]; names: (string | null)[]; fromAssemblyFile: boolean } {
  const parts = partsOf(version, "glb");
  if (parts.length < 2) {
    return { urls: [glbUrl(version.id)], parts: [], names: [], fromAssemblyFile: false };
  }
  const ordinals = parts.map((a) => a.part);
  const names = ordinals.map(
    (part) => version.parts?.find((p) => p.index === part)?.name ?? null,
  );
  if (!wholeRefused && hasWholeAssembly(version, "assembly_glb")) {
    return {
      urls: [wholeAssemblyUrl(version.id, "assembly_glb")],
      parts: ordinals,
      names,
      fromAssemblyFile: true,
    };
  }
  return {
    urls: parts.map((a) => artifactUrl(version.id, "glb", a.part)),
    parts: ordinals,
    names,
    fromAssemblyFile: false,
  };
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
