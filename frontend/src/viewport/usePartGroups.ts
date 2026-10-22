/** Each part's own group, by ordinal, kept as the part list says.
 *
 * Kept apart from the Canvas so the rules can be exercised without one: a part
 * that mounts after the list hid it or picked it out starts that way; one that
 * goes is put back as loaded; and what is hidden or picked out is forgotten when
 * the version changes, because it names parts by ordinal and the same ordinal on
 * another version is another part. */
import { type MutableRefObject, useCallback, useEffect, useRef } from "react";
import * as THREE from "three";

import { followPartView, restorePart, showPart } from "./partView";
import { viewportStore } from "./viewportStore";

export function usePartGroups(
  versionId: number | null,
  accent: string,
): {
  /** Each part's own group by ordinal, for the player to move. A ref rather than
   * state: it changes as parts mount, which is no reason to render. */
  partGroups: MutableRefObject<Map<number, THREE.Group>>;
  onPartGroup: (part: number, group: THREE.Group | null) => void;
} {
  const partGroups = useRef(new Map<number, THREE.Group>());
  // The colour a part is picked out in, read when it is applied so a change of
  // theme reaches the next one.
  const highlight = useRef(new THREE.Color());
  highlight.current.set(accent);

  const onPartGroup = useCallback((part: number, group: THREE.Group | null) => {
    if (group) {
      partGroups.current.set(part, group);
      const { hiddenParts, highlightedPart } = viewportStore.get();
      showPart(group, part, hiddenParts, highlightedPart, highlight.current);
    } else {
      const gone = partGroups.current.get(part);
      // Put back as loaded: a part drawn from its own file is lit on the loaded
      // file's material, which outlives this mount.
      if (gone) restorePart(gone);
      partGroups.current.delete(part);
    }
  }, []);

  useEffect(() => followPartView(partGroups.current, () => highlight.current), []);
  useEffect(() => viewportStore.resetParts(), [versionId]);

  return { partGroups, onPartGroup };
}
