/** Cameras + orbit controls.
 *
 * Perspective uses the Canvas default camera (the proven path). Orthographic
 * injects a drei OrthographicCamera(makeDefault) only while active; on unmount
 * drei restores the perspective default. OrbitControls(makeDefault) powers both
 * orbit/pan/zoom and the ViewCube. We refit on projection change. */
import { OrbitControls, OrthographicCamera } from "@react-three/drei";
import { useEffect, useRef } from "react";

import { useViewport, viewportStore } from "./viewportStore";

export function CameraRig() {
  const projection = useViewport((s) => s.projection);
  const first = useRef(true);

  // Reframe on projection *change* (not initial mount — Bounds handles first fit).
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    viewportStore.fit();
  }, [projection]);

  return (
    <>
      {projection === "orthographic" && (
        <OrthographicCamera makeDefault position={[80, 80, 80]} near={-20000} far={20000} zoom={10} />
      )}
      <OrbitControls makeDefault enableDamping />
    </>
  );
}
