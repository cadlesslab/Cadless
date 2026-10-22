/** react-three-fiber viewport.
 *
 * GLB load + grid/axes + orbit.: ViewCube, named-view presets,
 * fit/reset, ortho/perspective (CameraRig + ViewCommands + the overlay toolbar). */
import { Bounds, GizmoHelper, GizmoViewcube, Grid, Html } from "@react-three/drei";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import {
  Component,
  Suspense,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import * as THREE from "three";

import { setThumbnail } from "../panels/thumbnails";

import { useActiveVersion, useStoreSelector } from "../state";
import { AssemblyPlayer } from "./AssemblyPlayer";
import { CameraRig } from "./CameraRig";
import { Appearance, Ground, SceneEnvironment } from "./effects";
import { Measure, SectionPlane, TriangleCounter } from "./inspectionScene";
import { ModelParts } from "./ModelParts";
import { firstDrawnHitOn, showsModelAsItIs } from "./partView";
import { PreviewBanner } from "./PreviewBanner";
import { Readout } from "./Readout";
import { useAssemblyMotion } from "./useAssemblyMotion";
import { usePartGroups } from "./usePartGroups";
import { usePreviewSubject } from "./usePreviewSubject";
import { ViewCommands } from "./ViewCommands";
import { ViewportToolbar } from "./ViewportToolbar";
import { useViewport, viewportStore } from "./viewportStore";
import type { ThreeEvent } from "@react-three/fiber";

/** Read a CSS custom property, recomputed whenever the theme changes. */
function useCssVar(name: string): string {
  const theme = useStoreSelector((s) => s.theme);
  const [value, setValue] = useState("#0f1115");
  useEffect(() => {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    if (v) setValue(v);
  }, [name, theme]);
  return value;
}

class GltfErrorBoundary extends Component<
  { children: ReactNode; onFailed: () => void },
  { failed: boolean }
> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  componentDidCatch() {
    // Reported upward rather than only swallowed. Rendering nothing and telling
    // nobody leaves a canvas that is blank for a reason the person cannot see —
    // and a preview that never arrives looks exactly like a model with no
    // shape to it.
    this.props.onFailed();
  }
  componentDidUpdate(prev: { children: ReactNode }) {
    if (prev.children !== this.props.children && this.state.failed) {
      this.setState({ failed: false });
    }
  }
  render() {
    return this.state.failed ? null : this.props.children;
  }
}

/** Capture a thumbnail of the framed model a few frames after it loads.
 *
 * Only frames that show the model as it is are counted, and waiting here rather
 * than mounting this later keeps it to one picture per model. */
function ThumbnailCapture({ versionId }: { versionId: number }) {
  const gl = useThree((s) => s.gl);
  const frames = useRef(0);
  const done = useRef(false);
  useFrame(() => {
    if (done.current) return;
    if (!showsModelAsItIs(viewportStore.get())) {
      frames.current = 0;
      return;
    }
    if (++frames.current > 3) {
      done.current = true;
      try {
        setThumbnail(versionId, gl.domElement.toDataURL("image/png"));
      } catch {
        /* tainted/again later */
      }
    }
  });
  return null;
}

export function Viewport() {
  const version = useActiveVersion();
  const bg = useCssVar("--viewport-bg");
  const gridMajor = useCssVar("--grid-major");
  const gridMinor = useCssVar("--grid-minor");
  const modelRef = useRef<THREE.Group>(null);

  const preview = useViewport((s) => s.preview);
  const { subject, onFailed } = usePreviewSubject(version, preview);
  const urls = subject.urls;
  const gridVisible = useViewport((s) => s.gridVisible);
  const axesVisible = useViewport((s) => s.axesVisible);

  const motion = useAssemblyMotion(version, subject.previewing, subject.parts);
  const accent = useCssVar("--accent");
  const { partGroups, onPartGroup } = usePartGroups(version?.id ?? null, accent);
  // Where the parts reach fully apart, for the ground to cover as well.
  const [apartExtent, setApartExtent] = useState<THREE.Box3 | null>(null);

  return (
    <div className="viewport" data-testid="viewport">
      <Canvas
        camera={{ fov: 50, position: [80, 80, 80] }}
        dpr={[1, 2]}
        shadows
        gl={{ preserveDrawingBuffer: true, logarithmicDepthBuffer: true }}
      >
        <color attach="background" args={[bg]} />
        <ambientLight intensity={0.4} />
        <directionalLight position={[50, 100, 75]} intensity={0.8} />
        <SceneEnvironment />
        {gridVisible && (
          <Grid
            args={[200, 200]}
            cellColor={gridMinor}
            sectionColor={gridMajor}
            infiniteGrid
            fadeDistance={600}
            cellSize={10}
            sectionSize={50}
          />
        )}
        {axesVisible && <axesHelper args={[20]} />}

        {urls.length > 0 && (
          <GltfErrorBoundary onFailed={onFailed}>
            <Suspense
              fallback={
                <Html center>
                  <span className="vp-loading">Loading model…</span>
                </Html>
              }
            >
              {/* Bounds lives inside Suspense so it only ever frames a loaded
                  model (fitting an empty scene yields a NaN camera). */}
              {/* Ground + Measure add non-model geometry (shadow plane, marker
                  spheres); they live OUTSIDE Bounds so the observe refit that
                  fires on every canvas resize measures the model alone. */}
              <Bounds key={JSON.stringify(urls)} fit clip observe margin={1.25}>
                <group
                  ref={modelRef}
                  onPointerDown={(e: ThreeEvent<PointerEvent>) => {
                    if (viewportStore.get().tool !== "measure") return;
                    e.stopPropagation();
                    const hit = firstDrawnHitOn(e.intersections, e.eventObject);
                    if (hit) viewportStore.addMeasurePoint([hit.point.x, hit.point.y, hit.point.z]);
                  }}
                >
                  <ModelParts
                    urls={urls}
                    parts={subject.parts}
                    fromAssemblyFile={subject.fromAssemblyFile}
                    names={subject.names}
                    onPartGroup={onPartGroup}
                    onUnusable={onFailed}
                  />
                </group>
                <ViewCommands modelRef={modelRef} />
                <Appearance modelRef={modelRef} />
                <SectionPlane modelRef={modelRef} />
                <TriangleCounter modelRef={modelRef} />
                {subject.captureVersionId != null && (
                  <ThumbnailCapture versionId={subject.captureVersionId} />
                )}
                {/* Inside Bounds so it remounts with every model under the
                    Bounds key, and can widen the framing when parts leave. */}
                {motion && (
                  <AssemblyPlayer
                    motion={motion}
                    modelRef={modelRef}
                    partGroups={partGroups}
                    onApartBox={setApartExtent}
                  />
                )}
              </Bounds>
              <Ground modelRef={modelRef} extent={apartExtent} />
              <Measure />
            </Suspense>
          </GltfErrorBoundary>
        )}

        <CameraRig />
        <GizmoHelper alignment="top-right" margin={[64, 64]}>
          <GizmoViewcube />
        </GizmoHelper>
      </Canvas>

      <ViewportToolbar />
      {/* One row rather than two corners — see `.vp-overlays`. */}
      <div className="vp-overlays">
        <Readout />
        <PreviewBanner />
      </div>

      {subject.emptyReason && <div className="viewport-empty">{subject.emptyReason}</div>}
    </div>
  );
}
