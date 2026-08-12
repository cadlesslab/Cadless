/** The brand mark is drawn twice — once as a React component and once as a
 *  standalone file, because a favicon is fetched on its own and cannot import
 *  anything. Two copies of one drawing is exactly the kind of duplication that
 *  rots in silence: edit the component and every test still passes while the
 *  browser tab keeps showing the old mark. Both files say in prose that they
 *  are the same drawing; this is what makes that claim fail when it stops
 *  being true.
 *
 *  Read through Vite's `?raw` rather than `node:fs`, which would mean adding
 *  `@types/node` to a package that has managed without it. */
import { describe, expect, it } from "vitest";

import faviconSource from "../../public/favicon.svg?raw";
import iconsSource from "./icons.tsx?raw";

/** Every `d` attribute, in document order. Order matters as much as the set:
 *  the two side faces carry different opacities, so swapping them would light
 *  the solid from the wrong side while the paths still matched. */
function paths(svg: string): string[] {
  return [...svg.matchAll(/\sd="([^"]+)"/g)].map((m) => m[1].replace(/\s+/g, " ").trim());
}

describe("the brand mark", () => {
  it("is the same drawing in the component and in the favicon", () => {
    const favicon = paths(faviconSource);
    // The lit top face and the two side faces.
    expect(favicon).toHaveLength(3);
    // `icons.tsx` holds every other glyph too, so the mark's three paths are
    // looked for inside it rather than compared against the whole file.
    const component = paths(iconsSource);
    for (const d of favicon) expect(component).toContain(d);
  });
});
