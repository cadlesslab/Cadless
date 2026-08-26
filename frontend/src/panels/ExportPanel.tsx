/** Getting something out: a file, a link, or a physical object.
 *
 * These three used to be in two places — the formats, Print and Share inside
 * the Details inspector, and the printer's address over in Settings — and
 * neither place was called export. Somebody who wants an object out of this
 * had to know that "Details" was where the printing was.
 *
 * The version comes from {@link useActiveVersion} rather than a prop, because a
 * registered panel is mounted by the rail with no parent to hand it one. That
 * is the same hook the inspector uses, so the two cannot disagree about which
 * version is on screen.
 */
import { useEffect, useState } from "react";

import { fetchPrintCapability } from "../api";
import { EmptyState, Panel } from "../components";
import { useActiveVersion } from "../state";
import { availableFormats } from "./exportFormats";
import { ExportShare } from "./ExportShare";
import { PrinterSettings } from "./PrinterSettings";

export function ExportPanel() {
  const version = useActiveVersion();
  // Whether an address can be recorded here at all, which is not the same
  // question as whether one has been. On a hosted build the settings routes are
  // refused before the engine sees them, so mounting the printer panel there
  // spent a request on a 401 and put the failure on screen as an error the
  // reader could do nothing about. `can_configure` exists for exactly this
  // distinction; it was already deciding the sentence in the print dialog and
  // not this. Undecided until the answer arrives, so nothing flashes.
  const [canConfigure, setCanConfigure] = useState<boolean | null>(null);
  useEffect(() => {
    let alive = true;
    fetchPrintCapability()
      .then((c) => alive && setCanConfigure(c.can_configure))
      // A capability that cannot be read is not a licence to ask for settings:
      // the panel simply does not offer them. **Not covered by a test.** A
      // rejected mock in this suite is reported as an unhandled rejection by
      // the runner even with a handler attached to it, so the branch is left
      // asserted only by reading -- said here rather than left to look covered.
      .catch(() => alive && setCanConfigure(false));
    return () => {
      alive = false;
    };
  }, []);
  // `ok` is not enough: `ExportShare` renders nothing at all when the version
  // carries no exportable artifact, and a panel with neither the actions nor a
  // word about why is worse than one that says there is nothing here.
  const exportable = version?.ok === true && availableFormats(version).length > 0;

  return (
    <Panel title="Export">
      {exportable && version ? (
        <ExportShare version={version} />
      ) : (
        // The printer settings stay reachable either way: setting an address is
        // something somebody does *before* there is anything to print, and a
        // panel that hid them until a model existed would send them looking in
        // Settings, which is where they no longer are.
        <EmptyState>
          {version ? "This version failed, so there is nothing to export." : "Nothing to export yet."}
        </EmptyState>
      )}
      {canConfigure === true && <PrinterSettings />}
    </Panel>
  );
}
