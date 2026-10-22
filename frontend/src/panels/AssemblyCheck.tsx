/** The assembly check's findings on one build, in the check's own words. */
import { useId } from "react";

import type { AssemblyFindings } from "../api";

/** What the assembly check found wrong with a build, and whether it was kept.
 *
 * Kept apart from any reply so a reader sees what was measured rather than what
 * a model chose to say about it, and with failures apart from what the check
 * could not establish: keeping the two apart lets a reader tell "checked and
 * wrong" from "could not be checked". A heading with nothing under it is left
 * out, and each list is named by its heading.
 *
 * Whether the build was kept is said because both happen: a model built as an
 * assembly is refused when its parts fail, while any other is handed over with
 * the findings on it, and the reader is the one left to act on them. */
export function AssemblyCheck({ failures, unchecked, kept }: AssemblyFindings & { kept: boolean }) {
  const id = useId();
  const sections = [
    { key: "failures", heading: "Not accepted", lines: failures },
    { key: "unchecked", heading: "Could not be established", lines: unchecked },
  ];
  return (
    // A group rather than a landmark: a turn can hold several, all with this name.
    <div className="assembly-check" role="group" aria-label="Assembly check">
      <p className="assembly-check-title">Assembly check</p>
      <p className="assembly-check-note">
        {kept ? "On the build that was kept." : "On a build that was not kept."}
      </p>
      {sections
        .filter((s) => s.lines.length > 0)
        .map((s) => (
          <div key={s.key}>
            <p className="assembly-check-heading" id={`${id}-${s.key}`}>
              {s.heading}
            </p>
            <ul className="assembly-check-list" aria-labelledby={`${id}-${s.key}`}>
              {s.lines.map((line, i) => (
                <li key={i}>{line}</li>
              ))}
            </ul>
          </div>
        ))}
    </div>
  );
}
