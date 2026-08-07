/** The viewport's subject, and the failure that changes it.
 *
 * Separated from the Canvas so the rule can be exercised without one. What is
 * drawn is decided by `viewerSubject`, which is pure; what is left here is the
 * one thing that is not — that a load can fail, which only the attempt knows,
 * and that a later attempt should be allowed to succeed. */
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
  // What the loader was asked for, worked out as if it had not failed. The
  // failure is applied afterwards, so this stays the thing a retry is measured
  // against rather than disappearing along with the model it names.
  const attempt = viewerSubject(version, preview, false);

  // A new address is a new attempt, and so is asking for the same one again —
  // which is what opening the same card twice is. Without the second, a preview
  // that failed once could never be shown again: the address has not changed,
  // so nothing would tell this to try.
  useEffect(() => setFailed(false), [attempt.url, preview]);

  const onFailed = useCallback(() => {
    // The loader keeps its answers by address, including the rejections, and
    // hands the stored one back on every later render for that key. Clearing it
    // is what makes trying again an actual attempt rather than a replay.
    if (attempt.url) useGLTF.clear(attempt.url);
    setFailed(true);
  }, [attempt.url]);

  return { subject: failed ? viewerSubject(version, preview, true) : attempt, onFailed };
}
