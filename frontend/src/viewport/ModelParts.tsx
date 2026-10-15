/** The model the viewport draws: every part of it, each its own object.
 *
 * Kept apart from the Canvas so that what is asked for can be tested without
 * one. */
import { useGLTF } from "@react-three/drei";
import { useCallback } from "react";
import type * as THREE from "three";

import { weldModel } from "./weld";

function Model({ url }: { url: string }) {
  const gltf = useGLTF(url);
  // OCCT GLBs arrive as one primitive per B-Rep face; unwelded seams shimmer
  // as dark cracks while orbiting. weldModel is idempotent on the cache.
  weldModel(gltf.scene);
  return <primitive object={gltf.scene} />;
}

/** One part inside a group of its own, which is what gets moved.
 *
 * The scene the loader hands back is cached by address and handed to every
 * mount of it, so moving that would move the part everywhere it is drawn and
 * leave it moved after this model is gone. The group belongs to this mount
 * alone, and is handed up by part ordinal so the player can find it. */
function Part({
  url,
  part,
  onPartGroup,
}: {
  url: string;
  part: number | undefined;
  onPartGroup?: (part: number, group: THREE.Group | null) => void;
}) {
  const register = useCallback(
    (group: THREE.Group | null) => {
      if (part !== undefined) onPartGroup?.(part, group);
    },
    [part, onPartGroup],
  );
  return (
    <group ref={register}>
      <Model url={url} />
    </group>
  );
}

/** One object per part rather than one merged mesh, so each piece of an
 * assembly stays a piece. `parts[i]` is the ordinal of `urls[i]`; a url with
 * none — a model drawn whole — is drawn the same, and handed up to nobody. */
export function ModelParts({
  urls,
  parts = [],
  onPartGroup,
}: {
  urls: string[];
  parts?: number[];
  onPartGroup?: (part: number, group: THREE.Group | null) => void;
}) {
  return (
    <>
      {urls.map((url, i) => (
        <Part key={url} url={url} part={parts[i]} onPartGroup={onPartGroup} />
      ))}
    </>
  );
}
