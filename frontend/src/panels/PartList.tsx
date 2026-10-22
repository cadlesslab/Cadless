/** The parts of the version open, by name and colour, each one hideable.
 *
 * Pointing at a part, or moving focus onto it, picks it out in the viewport.
 * The choices live in the viewport's store, keyed by part ordinal, because the
 * viewport is what acts on them; this only reads and sets them. */
import { useEffect } from "react";

import type { Version } from "../api";
import { Button } from "../components";
import { useViewport, viewportStore } from "../viewport/viewportStore";
import { partRows } from "./partRows";

export function PartList({ version }: { version: Version }) {
  const rows = partRows(version);
  const hidden = useViewport((s) => s.hiddenParts);
  // While something else is in the viewport — a catalogue's mesh, or its note
  // that it has none — these parts are not drawn, and a toggle that changes
  // nothing anyone can see reads as a broken one.
  const previewing = useViewport((s) => s.preview !== null);

  // A part left hidden or picked out when the list goes away would stay that
  // way with nothing on screen to say why, or to undo it.
  useEffect(
    () => () => {
      viewportStore.highlightPart(null);
      viewportStore.showAllParts();
    },
    [],
  );

  if (rows.length === 0) return null;
  const pick = (part: number | null) => viewportStore.highlightPart(previewing ? null : part);

  return (
    <section className="part-list" aria-label="Parts">
      <div className="part-list-head">
        <span className="part-list-title">Parts · {rows.length}</span>
        {hidden.length > 0 && (
          <Button
            size="sm"
            variant="ghost"
            disabled={previewing}
            onClick={() => viewportStore.showAllParts()}
          >
            Show all
          </Button>
        )}
      </div>
      <ul>
        {rows.map((row) => (
          <li
            key={row.part}
            onMouseEnter={() => pick(row.part)}
            onMouseLeave={() => pick(null)}
          >
            <label className="part-row">
              <input
                type="checkbox"
                checked={!hidden.includes(row.part)}
                disabled={previewing}
                onChange={() => viewportStore.togglePart(row.part)}
                onFocus={() => pick(row.part)}
                onBlur={() => pick(null)}
              />
              <span
                className={row.color ? "part-swatch" : "part-swatch none"}
                style={row.color ? { background: row.color } : undefined}
                title={row.color ? undefined : "No colour set"}
                data-testid="part-swatch"
              />
              <span className="part-name">{row.name}</span>
            </label>
          </li>
        ))}
      </ul>
    </section>
  );
}
