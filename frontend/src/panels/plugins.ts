/** Panels this build was handed from outside the tree.
 *
 * The backend has a seam a distribution can join at runtime: it declares a
 * `cadless.routers` entry point and the app finds it. **A bundle cannot work
 * that way.** JavaScript that nothing imports is not in the build at all, so
 * `registerPanel` — an ordinary function call — never runs. Discovery has to
 * happen while the bundle is being assembled, which means here.
 *
 * `import.meta.glob` is Vite's build-time directory read: it is resolved by the
 * bundler, not at runtime, so what it finds is compiled in. With `eager` the
 * modules are imported outright rather than behind a promise, which is what a
 * registration needs — the rail asks the registry for its panels as it renders,
 * and a registration still in flight is a panel that is not there.
 *
 * A build with no plugins matches nothing and this is an empty object. That is
 * the ordinary case: it is what the tree ships, and it costs the bundle
 * nothing.
 *
 * The directory is git-ignored on purpose. A plugin is placed here by whoever
 * is composing a build — it is not something this repository carries, and a
 * plugin accidentally committed would make the tree ship a panel it does not
 * own.
 */
const registered = import.meta.glob("../plugins/*/register.{ts,tsx}", { eager: true });

/** How many plugin modules this build compiled in.
 *
 * Exported for the test that pins the empty case. Reading it tells you nothing
 * about what registered — a module is free to register none, or several — so
 * it answers "was the seam wired", not "what is on screen".
 */
export const pluginModuleCount = Object.keys(registered).length;
