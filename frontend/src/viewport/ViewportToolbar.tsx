/** Slim viewport overlay: opacity + inspection tools (Orbit/Measure/Section)
 * and their sub-controls. Named views, fit/reset, projection,
 * display mode and grid/axes now live in the workspace Toolbar / View menu;
 * this overlay stays on the canvas next to the ViewCube for in-context tools. */
import { Button, SegmentedControl, Slider } from "../components";
import { useViewport, viewportStore } from "./viewportStore";

export function ViewportToolbar() {
  const opacity = useViewport((s) => s.opacity);
  const tool = useViewport((s) => s.tool);
  const sectionAxis = useViewport((s) => s.sectionAxis);
  const sectionOffset = useViewport((s) => s.sectionOffset);

  return (
    <div className="vp-toolbar" role="toolbar" aria-label="Viewport controls">
      <div className="vp-group vp-opacity" aria-label="Opacity">
        <span className="vp-label">Opacity</span>
        <Slider
          label="Opacity"
          min={0.1}
          max={1}
          step={0.05}
          value={opacity}
          onValueChange={(v) => viewportStore.setOpacity(v)}
        />
      </div>

      <SegmentedControl
        ariaLabel="Inspection tool"
        value={tool}
        onChange={(v) => viewportStore.setTool(v)}
        segments={[
          { value: "none", label: "Orbit" },
          { value: "measure", label: "Measure" },
          { value: "section", label: "Section" },
        ]}
      />

      {tool === "section" && (
        <div className="vp-group vp-section" aria-label="Section controls">
          <SegmentedControl
            ariaLabel="Section axis"
            value={sectionAxis}
            onChange={(v) => viewportStore.setSectionAxis(v)}
            segments={[
              { value: "x", label: "X" },
              { value: "y", label: "Y" },
              { value: "z", label: "Z" },
            ]}
          />
          <Slider
            label="Section offset"
            min={-1}
            max={1}
            step={0.02}
            value={sectionOffset}
            onValueChange={(v) => viewportStore.setSectionOffset(v)}
          />
        </div>
      )}

      {tool === "measure" && (
        <div className="vp-group" aria-label="Measure controls">
          <span className="vp-label">Click points to measure</span>
          <Button size="sm" variant="ghost" onClick={() => viewportStore.clearMeasure()}>
            Clear
          </Button>
        </div>
      )}
    </div>
  );
}
