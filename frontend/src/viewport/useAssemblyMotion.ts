/** The assembly motion of what the viewport is drawing, when it has one.
 *
 * Fetched only for a version whose rows say it carries one, so every other
 * version costs no request, and never while a preview is up — what is on
 * screen then is not this version, and its parts are not the ones the motion
 * names. Held against the version it was fetched for, so a new version shows no
 * control until its own answer arrives, and an answer that arrives after the
 * version changed is dropped.
 *
 * A failure of any kind — not found, the network, a payload that does not
 * describe the parts drawn — is no motion. The model is still drawn, just not
 * played, and an error toast about a control nobody asked for would be noise. */
import { useEffect, useMemo, useState } from "react";

import { getAssemblyMotion, hasAssemblyMotion, type Version } from "../api";
import { parseMotion, type Motion } from "./assemblyMotion";

export function useAssemblyMotion(
  version: Version | null,
  previewing: boolean,
  parts: number[],
): Motion | null {
  // Keyed on the id rather than the object, so a fresh copy of the same
  // version does not ask again.
  const id = version && !previewing && hasAssemblyMotion(version) ? version.id : null;
  const [fetched, setFetched] = useState<{ id: number; raw: unknown } | null>(null);

  useEffect(() => {
    if (id == null) return;
    let alive = true;
    getAssemblyMotion(id).then(
      (raw) => alive && setFetched({ id, raw }),
      // Recorded as an answer of nothing rather than left unset, so the
      // rejection is handled here and says what it means: no control.
      () => alive && setFetched({ id, raw: null }),
    );
    return () => {
      alive = false;
    };
  }, [id]);

  // The parts arrive as a new list on every render, so they are compared by
  // what they hold. Compared as a list, the motion would be rebuilt on every
  // render, and the player resets the play each time it is handed a new one.
  const drawn = JSON.stringify(parts);
  return useMemo(
    () =>
      fetched && fetched.id === id ? parseMotion(fetched.raw, JSON.parse(drawn) as number[]) : null,
    [fetched, id, drawn],
  );
}
