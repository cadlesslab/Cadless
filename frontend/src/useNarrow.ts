/** Whether the workspace is too narrow to hold two columns side by side.
 *
 * The stylesheet already knows this width, so why ask again in JavaScript: CSS
 * can move the chat panel over the viewport, but it cannot decide that the
 * panel starts closed and is opened by a control that exists only here. That is
 * a rendering decision, and it needs a boolean React can branch on. */
import { useEffect, useState } from "react";

/** Kept in step with the `max-width: 720px` block in `styles/app.css` — below
 *  this the rail and a 320px chat column leave the 3D viewport unusable. */
export const NARROW_QUERY = "(max-width: 720px)";

export function useNarrow(): boolean {
  // Read during the first render rather than in an effect afterwards. Seeded
  // from an effect, every phone would paint the desktop layout for one frame.
  const [narrow, setNarrow] = useState(() => window.matchMedia(NARROW_QUERY).matches);

  useEffect(() => {
    const mql = window.matchMedia(NARROW_QUERY);
    const onChange = (e: MediaQueryListEvent) => setNarrow(e.matches);
    mql.addEventListener("change", onChange);
    // The width can have changed between the first render and this effect.
    setNarrow(mql.matches);
    return () => mql.removeEventListener("change", onChange);
  }, []);

  return narrow;
}
