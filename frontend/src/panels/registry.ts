/** Which panels this build offers, and what each one draws.
 *
 * A registry rather than a list in the rail, because not every build ships
 * every panel. Named in a `switch`, a panel is a file the rail imports — so a
 * build without that file does not compile, and a build that adds one has to
 * edit the rail to say so. Registered, a panel is something a module hands in
 * at load, and the rail neither knows nor asks which ones exist.
 *
 * Order is registration order: a `Map` keeps its insertion order, so what
 * registers first sits highest in the rail. That makes the order a property of
 * who registers when, which is what a build adding a panel needs it to be.
 */
import type { ReactNode } from "react";

/** A registered panel's id.
 *
 * Any string a build has registered. This used to be a union of the eight the
 * rail knew, which is exactly what the registry exists to stop being true — and
 * the trade it makes: an id is no longer checked at compile time, so a typo in
 * one reaches `panelFor` rather than the type checker. What that costs is
 * bounded by `panelFor` answering nothing for an id no panel is registered
 * under, which is the same thing the old `switch` did at runtime for a value
 * outside its union. */
export type PanelId = string;

export interface PanelEntry {
  /** The rail's tooltip and accessible name. */
  label: string;
  /** The rail icon. */
  icon: ReactNode;
  /** Drawn into the flyout, and only once the panel is opened. */
  render: () => ReactNode;
}

const REGISTERED = new Map<PanelId, PanelEntry>();

/** Offer a panel under `id`, replacing one already registered under it.
 *
 * Replacing rather than refusing: a build that ships its own version of a panel
 * is registering it on purpose, and the alternative — first registration wins —
 * would make the outcome depend on module load order, which nobody controls.
 */
export function registerPanel(id: PanelId, entry: PanelEntry): void {
  REGISTERED.set(id, entry);
}

/** Withdraw a panel, so a build that registered one can take it back.
 *
 * The pair to `registerPanel`, on the same rule the domain registry follows:
 * a registry a test can only add to is one whose additions outlive the test
 * that made them. */
export function unregisterPanel(id: PanelId): void {
  REGISTERED.delete(id);
}

/** The panel registered under `id`, or nothing when this build has none.
 *
 * Nothing rather than a throw. An id now arrives as an unchecked string, and a
 * build ships whichever panels it registered — so "no panel answers to this"
 * is an ordinary state, not a bug, and it should draw an empty flyout rather
 * than fail to render.
 */
export function panelFor(id: PanelId): PanelEntry | undefined {
  return REGISTERED.get(id);
}

/** Every registered panel, in the order it was registered. */
export function registeredPanels(): { id: PanelId; entry: PanelEntry }[] {
  return [...REGISTERED].map(([id, entry]) => ({ id, entry }));
}
