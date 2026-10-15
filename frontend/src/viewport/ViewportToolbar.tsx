/** Slim viewport overlay: opacity + inspection tools (Orbit/Measure/Section)
 * and their sub-controls, and the assembly controls when the model has a
 * motion to play. Named views, fit/reset, projection,
 * display mode and grid/axes now live in the workspace Toolbar / View menu;
 * this overlay stays on the canvas next to the ViewCube for in-context tools. */
import { Button, SegmentedControl, Slider } from "../components";
import { useViewport, viewportStore } from "./viewportStore";

/** How finely the assembly slider moves: fine enough to stop a part anywhere
 * along its joint, not only at its ends. */
const SCRUB_STEP = 0.01;

export function ViewportToolbar() {
  const opacity = useViewport((s) => s.opacity);
  const tool = useViewport((s) => s.tool);
  const sectionAxis = useViewport((s) => s.sectionAxis);
  const sectionOffset = useViewport((s) => s.sectionOffset);
  const assemblySteps = useViewport((s) => s.assemblySteps);
  const assemblyPosition = useViewport((s) => s.assemblyPosition);

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

      {assemblySteps > 0 && (
        <AssemblyControls steps={assemblySteps} position={assemblyPosition} />
      )}
    </div>
  );
}

/** Play, step and scrub an assembly apart and back together.
 *
 * Only offered for a model that has joints to play, so a model without a
 * measured assembly looks exactly as it did. Each button is disabled where it
 * would do nothing, rather than left to be pressed for no answer. */
function AssemblyControls({ steps, position }: { steps: number; position: number }) {
  const assembled = position <= 0;
  const apart = position >= steps;
  return (
    <div className="vp-group vp-assembly" role="group" aria-label="Assembly">
      <Button
        size="sm"
        variant="ghost"
        disabled={apart}
        onClick={() => viewportStore.playAssembly("apart")}
      >
        Take apart
      </Button>
      <Button
        size="sm"
        variant="ghost"
        disabled={assembled}
        onClick={() => viewportStore.playAssembly("together")}
      >
        Put together
      </Button>
      <Button
        size="sm"
        variant="ghost"
        disabled={assembled}
        onClick={() => viewportStore.stepAssembly(-1)}
      >
        Step back
      </Button>
      <Button
        size="sm"
        variant="ghost"
        disabled={apart}
        onClick={() => viewportStore.stepAssembly(1)}
      >
        Step forward
      </Button>
      <Slider
        label="Assembly step"
        min={0}
        max={steps}
        step={SCRUB_STEP}
        value={position}
        onValueChange={(v) => {
          // A scrub is a place picked by hand; left playing, the next frame
          // would carry the parts on past it.
          viewportStore.stopAssembly();
          viewportStore.setAssemblyPosition(v);
        }}
      />
      {/* The joint in motion, or the one just finished: a part on its way
          through joint 2 is part of joint 2, not of joint 1. */}
      <span className="vp-label vp-assembly-at">
        {assembled ? "Assembled" : `Joint ${Math.ceil(position)} of ${steps}`}
      </span>
    </div>
  );
}
