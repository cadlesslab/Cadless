/** View flyout: named-view presets, fit/reset, projection, display mode and
 * grid/axes visibility. Drives the shared viewportStore (same bus as the
 * in-canvas ViewCube + overlay). Replaces the old top-bar View menu. */
import { Button, Panel, SegmentedControl } from "../components";
import type { ViewName } from "../viewport/math";
import { useViewport, viewportStore } from "../viewport/viewportStore";

const PRESETS: { view: ViewName; label: string }[] = [
  { view: "iso", label: "Iso" },
  { view: "front", label: "Front" },
  { view: "back", label: "Back" },
  { view: "right", label: "Right" },
  { view: "left", label: "Left" },
  { view: "top", label: "Top" },
  { view: "bottom", label: "Bottom" },
];

export function ViewPanel() {
  const projection = useViewport((s) => s.projection);
  const displayMode = useViewport((s) => s.displayMode);
  const gridVisible = useViewport((s) => s.gridVisible);
  const axesVisible = useViewport((s) => s.axesVisible);

  return (
    <Panel title="View">
      <div className="view-panel">
        <div className="view-group">
          <span className="view-label">Orientation</span>
          <div className="view-presets" role="group" aria-label="Named views">
            {PRESETS.map((p) => (
              <button
                key={p.view}
                className="view-preset"
                onClick={() => viewportStore.view(p.view)}
                title={`${p.label} view`}
              >
                {p.label}
              </button>
            ))}
          </div>
          <div className="view-actions">
            <Button size="sm" variant="ghost" onClick={() => viewportStore.fit()}>
              Zoom to fit
            </Button>
            <Button size="sm" variant="ghost" onClick={() => viewportStore.reset()}>
              Reset
            </Button>
          </div>
        </div>

        <div className="view-group">
          <span className="view-label">Projection</span>
          <SegmentedControl
            ariaLabel="Projection"
            value={projection}
            onChange={(v) => viewportStore.setProjection(v)}
            segments={[
              { value: "perspective", label: "Perspective" },
              { value: "orthographic", label: "Ortho" },
            ]}
          />
        </div>

        <div className="view-group">
          <span className="view-label">Display</span>
          <SegmentedControl
            ariaLabel="Display mode"
            value={displayMode}
            onChange={(v) => viewportStore.setDisplayMode(v)}
            segments={[
              { value: "shaded", label: "Shaded" },
              { value: "wireframe", label: "Wire" },
              { value: "xray", label: "X-ray" },
            ]}
          />
        </div>

        <div className="view-group">
          <span className="view-label">Visibility</span>
          <div className="view-toggles">
            <button
              className={`view-toggle ${gridVisible ? "on" : ""}`}
              aria-pressed={gridVisible}
              onClick={() => viewportStore.setGridVisible(!gridVisible)}
            >
              Grid
            </button>
            <button
              className={`view-toggle ${axesVisible ? "on" : ""}`}
              aria-pressed={axesVisible}
              onClick={() => viewportStore.setAxesVisible(!axesVisible)}
            >
              Axes
            </button>
          </div>
        </div>
      </div>
    </Panel>
  );
}
