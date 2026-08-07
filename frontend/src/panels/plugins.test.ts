/** The seam reads a directory that is not there, and that is the normal case.
 *
 * `import.meta.glob` is resolved by the bundler, so a pattern matching nothing
 * has to be a no-op rather than an error — otherwise this tree does not build
 * at all until somebody creates an empty directory to satisfy it, which is the
 * opposite of optional.
 *
 * These assertions describe **what this repository ships**. A composed build
 * that has been given a plugin sees a different number here, and that is the
 * point of the seam rather than a failure of it.
 */
import { describe, expect, it } from "vitest";

// The rail's own source, as text. `?raw` rather than `node:fs`, because this
// package type-checks against `vite/client` alone — pulling in Node's types to
// read one file would widen what the whole frontend is allowed to assume.
import leftRail from "./LeftRail.tsx?raw";
import { pluginModuleCount } from "./plugins";

describe("the plugin seam", () => {
  it("compiles in nothing when no plugin was placed", () => {
    expect(pluginModuleCount).toBe(0);
  });

  // Read from source rather than exercised, because with no plugin present
  // there is no observable difference to assert on: the whole seam is one
  // side-effect import, and deleting it leaves every other test, the type
  // check and the lint green while the rail silently stops accepting panels.
  // A source assertion is the only thing that fails when the wire is cut.
  it("is wired into the rail, after the built-ins", () => {
    // Anchored to the start of a line, so a commented-out import does not
    // satisfy it. A plain substring search does — which is how the first
    // version of this test passed with the seam switched off.
    const builtins = leftRail.search(/^import "\.\/builtins";$/m);
    const plugins = leftRail.search(/^import "\.\/plugins";$/m);
    expect(builtins).toBeGreaterThan(-1);
    expect(plugins).toBeGreaterThan(-1);
    // Order is the contract, not an accident: the registry replaces on a
    // repeated id, so whichever imports last is the one that can override.
    expect(builtins).toBeLessThan(plugins);
  });
});
