/** The viewport's subject, and the failure that changes it.
 *
 * Separated from the Canvas so the rule can be exercised without one. What is
 * drawn is decided by `viewerSubject`, which is pure; what is left here is the
 * one thing that is not — that a load can fail, which only the attempt knows,
 * and that a later attempt should be allowed to succeed. A whole-assembly file
 * that fails is not the end of it: the version still has a file per part, and
 * those are tried before the viewport says the model could not be loaded. */
import { useGLTF } from "@react-three/drei";
import { useCallback, useEffect, useState } from "react";

import type { Version } from "../api";
import { viewerSubject, type ViewerSubject } from "./preview";
import type { Preview } from "./viewportStore";

export function usePreviewSubject(
  version: Version | null,
  preview: Preview | null,
): { subject: ViewerSubject; onFailed: () => void } {
  const [failed, setFailed] = useState(false);
  // The attempt whose whole-assembly file would not do, by its addresses, rather
  // than a flag: a flag is cleared a render late, so the next version's first
  // render would ask for its part files before its own whole file. Forgotten
  // with the attempt, like a failure, so a version opened again tries its whole
  // file again rather than losing its colours for the rest of the session.
  const [refusedFor, setRefusedFor] = useState<string | null>(null);
  // What the loader was asked for, worked out as if it had not failed. The
  // failure is applied afterwards, so this stays the thing a retry is measured
  // against rather than disappearing along with the model it names.
  const attempt = viewerSubject(version, preview, false);
  // The list is built afresh on every render, so it is compared by what it
  // holds. Compared as an array, every render would be a new attempt, and a
  // failure would be undone the moment it was reported.
  const addresses = JSON.stringify(attempt.urls);
  const wholeRefused = refusedFor === addresses;

  // A new set of addresses is a new attempt, and so is a preview handed over
  // again at the same address — which is what opening the same card twice is.
  // Without the second, a preview that failed once could never be shown again:
  // its address has not changed, so nothing would tell this to try.
  useEffect(() => {
    setFailed(false);
    setRefusedFor(null);
  }, [addresses, preview]);

  // What is on screen now: the attempt, or the files of each part once the
  // whole-assembly file would not do.
  const current = wholeRefused ? viewerSubject(version, preview, false, true) : attempt;
  const shown = JSON.stringify(current.urls);
  const fromAssemblyFile = current.fromAssemblyFile;

  const onFailed = useCallback(() => {
    // The loader keeps its answers by address, including the rejections, and
    // hands the stored one back on every later render for that key. Clearing
    // every part is what makes trying again an actual attempt rather than a
    // replay of the one that failed beside parts that did not.
    for (const url of JSON.parse(shown) as string[]) useGLTF.clear(url);
    if (fromAssemblyFile) setRefusedFor(addresses);
    else setFailed(true);
  }, [shown, fromAssemblyFile, addresses]);

  return {
    subject: failed ? viewerSubject(version, preview, true, wholeRefused) : current,
    onFailed,
  };
}
