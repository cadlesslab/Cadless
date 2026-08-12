/** What a build offers at the bottom of the activity rail.
 *
 * A second registry rather than a flag on `PanelEntry`, because the two are
 * different things. A panel is an icon that opens a flyout, and the rail owns
 * both halves of that. A rail-bottom control is a whole control — it decides
 * what pressing it does, and it may not open anything at all. Giving `panelFor`
 * an entry that renders no flyout would leave every caller of it asking which
 * kind it had.
 *
 * The shape deliberately mirrors `registry.ts`: register replaces rather than
 * refuses, withdrawing is possible so a test's additions do not outlive it, and
 * reading answers in registration order. Two seams a build meets on the same
 * day should not behave differently for no reason.
 *
 * What is not here: `HelpButton` and the theme toggle. They are this app's own
 * controls rather than a build's, and the rail goes on drawing them directly —
 * a registry that also held them would let a plugin replace them by id.
 */
import type { ReactNode } from "react";

/** A registered control's id. An unchecked string, for the reason `PanelId`
 *  gives: the registry exists so the rail need not know the set. */
export type RailControlId = string;

export interface RailControlEntry {
  /** Drawn into the rail's bottom section. It is the whole control, including
   *  whatever accessible name it carries — the rail adds none, because it does
   *  not know what the control does.
   *
   *  The rail mounts this as a component rather than calling it, so it has a
   *  hook scope of its own: state inside it survives the rail re-rendering, and
   *  registering or withdrawing another control cannot disturb it. Calling it
   *  inline would put those hooks in the rail's own scope, where a second
   *  registration is "rendered more hooks than during the previous render". */
  render: () => ReactNode;
}

const REGISTERED = new Map<RailControlId, RailControlEntry>();

/** Offer a control under `id`, replacing one already registered under it.
 *
 * Replacing rather than refusing, as with panels: a build shipping its own
 * version is doing it on purpose, and first-registration-wins would make the
 * outcome depend on module load order.
 *
 * Register at module load, the way the panel seam does. The rail reads this
 * registry while it renders and subscribes to nothing, so a control registered
 * after first paint stays invisible until something else re-renders the rail.
 * Whether somebody is signed in belongs inside the control, not in whether it
 * was registered.
 *
 * Returns a withdrawal, matching `registerRequestHeaders` — a caller that keeps
 * the handle need not also keep the id. */
export function registerRailControl(id: RailControlId, entry: RailControlEntry): () => void {
  REGISTERED.set(id, entry);
  return () => {
    // Only if it is still the one registered: a later registration under the
    // same id belongs to whoever made it, and a stale handle must not take it
    // away. Idempotent for the same reason.
    if (REGISTERED.get(id) === entry) REGISTERED.delete(id);
  };
}

/** Withdraw a control. The pair to `registerRailControl`. */
export function unregisterRailControl(id: RailControlId): void {
  REGISTERED.delete(id);
}

/** Every registered control, in the order it was registered. */
export function registeredRailControls(): { id: RailControlId; entry: RailControlEntry }[] {
  return [...REGISTERED].map(([id, entry]) => ({ id, entry }));
}
