/** Live metrics readout overlay: units, bbox, volume, triangles.
 *
 * What it is allowed to say depends on what the viewport is showing rather than
 * on what the app calls active — see `readoutFor`. */
import { measureText } from "../measures";
import { useActiveVersion } from "../state";
import { readoutFor } from "./preview";
import { useViewport } from "./viewportStore";

export function Readout() {
  const version = useActiveVersion();
  const tris = useViewport((s) => s.triangleCount);
  const preview = useViewport((s) => s.preview);
  const shown = readoutFor(version, tris, preview);
  if (!shown.shown) return null;
  const measures = measureText(shown.volume, shown.bbox);

  return (
    <div className="vp-readout" aria-label="Model metrics">
      {/* The unit belongs to the size, so it goes when the size goes; the volume
          carries its own. A triangle count is in no unit. */}
      {measures.size && <span className="vp-readout-unit">{measures.unit}</span>}
      {measures.size && <span>{measures.size}</span>}
      {measures.volume && <span>{measures.volume}</span>}
      {shown.triangles != null && <span>{shown.triangles.toLocaleString()} tris</span>}
    </div>
  );
}
