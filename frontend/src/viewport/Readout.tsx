/** Live metrics readout overlay: units, bbox, volume, triangles.
 *
 * What it is allowed to say depends on what the viewport is showing rather than
 * on what the app calls active — see `readoutFor`. */
import { useActiveVersion } from "../state";
import { readoutFor } from "./preview";
import { useViewport } from "./viewportStore";

export function Readout() {
  const version = useActiveVersion();
  const tris = useViewport((s) => s.triangleCount);
  const preview = useViewport((s) => s.preview);
  const shown = readoutFor(version, tris, preview);
  if (!shown.shown) return null;

  return (
    <div className="vp-readout" aria-label="Model metrics">
      {/* The unit belongs to the measurements, so it goes when they go. A
          triangle count is not in millimetres. */}
      {(shown.bbox || shown.volume != null) && <span className="vp-readout-unit">mm</span>}
      {shown.bbox && <span>{shown.bbox.map((d) => d.toFixed(1)).join(" × ")}</span>}
      {shown.volume != null && <span>{shown.volume.toFixed(1)} mm³</span>}
      {shown.triangles != null && <span>{shown.triangles.toLocaleString()} tris</span>}
    </div>
  );
}
