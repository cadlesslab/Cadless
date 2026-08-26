/** Streaming a sliced job to a printer plugged into this machine.
 *
 * Its own hook because it is a whole flow rather than a handler: a device
 * chooser, a handshake that may walk several baud rates, a download, and then
 * tens of thousands of acknowledged lines with a Stop that has to reach all of
 * it. None of that is about offering export formats, which is what the panel
 * around it does.
 */
import { type RefObject, useState } from "react";

import { gcodeUrl } from "../api";
import { useToast } from "../components";
import { errMessage } from "../errors";
import { fetchGcode } from "./download";
import { handshake, type PrinterPort, requestPrinterPort, streamJob } from "./usbPrinter";

/** A print leaving through this tab's own USB connection.
 *
 * In state rather than in a ref because the dialog showing it is the only thing
 * telling the reader a print is running, and it has to re-render as the count
 * moves. The controller travels with it so Stop reaches the stream that is
 * actually running rather than one started after it.
 */
export type UsbJob = {
  phase: "connecting" | "printing";
  sent: number;
  total: number;
  controller: AbortController;
} | null;

/** Something the reader has to go and do, handed back for the caller to show. */
type Notice = { title: string; body: string } | null;

/** @param stillWanted what the caller's dialog is describing *now*, read after
 *   the device chooser closes — see the check inside `start`.
 * @param onCommitted called at the moment the print becomes real, so the
 *   caller can put its own dialog away. Not a return value: it has to happen
 *   before the streaming, which is the long part.
 */
export function useUsbPrint(
  stillWanted: RefObject<{ versionId: number } | null>,
  onCommitted: () => void,
) {
  const toast = useToast();
  const [job, setJob] = useState<UsbJob>(null);
  // True only while the device chooser is up. The caller's dialog stays open
  // behind it -- dismissing the chooser has to leave something to try again
  // from -- so without this a second click opens a second chooser and races a
  // second handshake at the same port.
  const [choosing, setChoosing] = useState(false);

  /** Print through this tab, to a printer plugged into this machine.
   *
   * The order of the first three steps is load-bearing, and each has a
   * different reason:
   *
   * 1. `requestPrinterPort` spends the click's user activation, so it goes
   *    first. Awaiting anything before it — the G-code, a capability check —
   *    leaves the chooser with no gesture left to open on.
   * 2. The handshake goes before the fetch because a port that turns out not to
   *    be a printer should cost one exchange rather than a whole download.
   * 3. Only then does the job leave the server, and it is the same job the
   *    Download button would have saved.
   */
  async function start(versionId: number): Promise<Notice> {
    if (choosing) return null;

    let port: PrinterPort;
    setChoosing(true);
    try {
      port = await requestPrinterPort();
    } catch {
      // The chooser was dismissed, or held nothing to choose. That is an answer
      // rather than a fault, so the caller's dialog stays open behind it.
      return null;
    } finally {
      setChoosing(false);
    }

    // Through the ref rather than anything captured when the button was
    // pressed: the dialog can be dismissed while the chooser is up, and a print
    // starting after somebody pressed Cancel is the one outcome that whole
    // dialog exists to prevent.
    if (stillWanted.current?.versionId !== versionId) return null;

    onCommitted();
    const controller = new AbortController();
    setJob({ phase: "connecting", sent: 0, total: 0, controller });
    try {
      // The signal from the first moment: `handshake` walks up to four baud
      // candidates at 2.5s each and opens a port on every one of them, and
      // every one of those waits is time Stop has to be able to reach.
      const shake = await handshake(port, undefined, controller.signal);
      if (!shake.ok || !shake.baudRate) {
        return {
          title: "That is not a printer",
          body: shake.detail ?? "Nothing on that port answered like a printer.",
        };
      }

      const gcode = await fetchGcode(gcodeUrl(versionId));
      setJob({ phase: "printing", sent: 0, total: 0, controller });

      let shownPercent = -1;
      const result = await streamJob(port, gcode, {
        baudRate: shake.baudRate,
        signal: controller.signal,
        onProgress: ({ sent, total }) => {
          // A real part is tens of thousands of lines, and a `setState` per
          // acknowledgement would spend the tab's frame budget on renders the
          // reader cannot perceive. A whole percent is the smallest step that
          // actually moves anything on screen.
          const percent = Math.floor((sent / total) * 100);
          if (percent === shownPercent && sent < total) return;
          shownPercent = percent;
          setJob({ phase: "printing", sent, total, controller });
        },
      });

      if (result.ok) toast.success("Printed over USB", result.detail);
      else if (result.stopped) toast.success("Print stopped", result.detail);
      else toast.error("The print stopped", result.detail);
      return null;
    } catch (err) {
      toast.error("The print stopped", errMessage(err));
      return null;
    } finally {
      setJob(null);
    }
  }

  return { job, choosing, start };
}
