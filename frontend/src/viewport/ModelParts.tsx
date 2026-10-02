/** The model the viewport draws: every part of it, each its own object.
 *
 * Kept apart from the Canvas so that what is asked for can be tested without
 * one. */
import { useGLTF } from "@react-three/drei";

import { weldModel } from "./weld";

function Model({ url }: { url: string }) {
  const gltf = useGLTF(url);
  // OCCT GLBs arrive as one primitive per B-Rep face; unwelded seams shimmer
  // as dark cracks while orbiting (#14). weldModel is idempotent on the cache.
  weldModel(gltf.scene);
  return <primitive object={gltf.scene} />;
}

/** One object per part rather than one merged mesh, so each piece of an
 * assembly stays a piece. */
export function ModelParts({ urls }: { urls: string[] }) {
  return (
    <>
      {urls.map((url) => (
        <Model key={url} url={url} />
      ))}
    </>
  );
}
