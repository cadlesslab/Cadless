/** The model the viewport draws: every part of it, each its own object.
 *
 * Kept apart from the Canvas so that what is asked for can be tested without
 * one. */
import { useGLTF } from "@react-three/drei";
import { type ReactNode, useCallback, useEffect, useMemo } from "react";
import type * as THREE from "three";

import { weldModel } from "./weld";
import { assemblyParts } from "./wholeAssembly";

function Model({ url }: { url: string }) {
  const gltf = useGLTF(url);
  // OCCT GLBs arrive as one primitive per B-Rep face; unwelded seams shimmer
  // as dark cracks while orbiting. weldModel is idempotent on the cache.
  weldModel(gltf.scene);
  return <primitive object={gltf.scene} />;
}

type OnPartGroup = (part: number, group: THREE.Group | null) => void;

/** One part inside a group of its own, which is what gets moved.
 *
 * The scene the loader hands back is cached by address and handed to every
 * mount of it, so moving that would move the part everywhere it is drawn and
 * leave it moved after this model is gone. The group belongs to this mount
 * alone, and is handed up by part ordinal so the player can find it. */
function PartGroup({
  part,
  onPartGroup,
  children,
}: {
  part: number | undefined;
  onPartGroup?: OnPartGroup;
  children: ReactNode;
}) {
  const register = useCallback(
    (group: THREE.Group | null) => {
      if (part !== undefined) onPartGroup?.(part, group);
    },
    [part, onPartGroup],
  );
  return <group ref={register}>{children}</group>;
}

/** A whole-assembly file drawn as its parts, each in a group of its own.
 *
 * `parts[i]` is the ordinal of the file's i-th part, and `names[i]` what the
 * version calls it. A file that does not hold that many separate parts, so
 * named, is not drawn, and `onUnusable` is told, so the viewport can go back to
 * the per-part files rather than move the wrong piece. */
function WholeAssembly({
  url,
  parts,
  names,
  onPartGroup,
  onUnusable,
}: {
  url: string;
  parts: number[];
  names: readonly (string | null)[];
  onPartGroup?: OnPartGroup;
  onUnusable?: () => void;
}) {
  const gltf = useGLTF(url);
  // Compared by what they hold: the list is built afresh on every render.
  const named = JSON.stringify(names);
  const owned = useMemo(
    () => assemblyParts(gltf, parts.length, JSON.parse(named) as (string | null)[]),
    [gltf, parts.length, named],
  );
  // Apart, so the materials go with the parts they belong to and not with a
  // change in who is told about a file that would not do.
  useEffect(() => () => owned?.dispose(), [owned]);
  useEffect(() => {
    if (!owned) onUnusable?.();
  }, [owned, onUnusable]);
  if (!owned) return null;
  return (
    <>
      {owned.objects.map((object, i) => (
        <PartGroup key={parts[i]} part={parts[i]} onPartGroup={onPartGroup}>
          <primitive object={object} />
        </PartGroup>
      ))}
    </>
  );
}

/** One object per part rather than one merged mesh, so each piece of an
 * assembly stays a piece. `parts[i]` is the ordinal of `urls[i]`; a url with
 * none — a model printed whole — is drawn the same, and handed up to nobody.
 * With `fromAssemblyFile`, `urls[0]` is one whole-assembly file whose parts are
 * `parts`, called `names` where the version names them. */
export function ModelParts({
  urls,
  parts = [],
  fromAssemblyFile = false,
  names = [],
  onPartGroup,
  onUnusable,
}: {
  urls: string[];
  parts?: number[];
  fromAssemblyFile?: boolean;
  names?: readonly (string | null)[];
  onPartGroup?: OnPartGroup;
  onUnusable?: () => void;
}) {
  if (fromAssemblyFile) {
    return (
      <WholeAssembly
        url={urls[0]}
        parts={parts}
        names={names}
        onPartGroup={onPartGroup}
        onUnusable={onUnusable}
      />
    );
  }
  return (
    <>
      {urls.map((url, i) => (
        <PartGroup key={url} part={parts[i]} onPartGroup={onPartGroup}>
          <Model url={url} />
        </PartGroup>
      ))}
    </>
  );
}
