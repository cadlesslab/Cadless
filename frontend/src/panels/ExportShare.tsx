/** Export format picker + share link + print. */
import { useState } from "react";

import {
  artifactUrl,
  type ArtifactKind,
  fetchPrintCapability,
  gcodeUrl,
  type PrintCapability,
  printHeaders,
  sendVersionToPrinter,
  type SliceResult,
  sliceVersion,
  type Version,
} from "../api";
import { Button, Modal, Tooltip, useToast } from "../components";
import { errMessage } from "../errors";
import { BASE_URL } from "../routing";
import { availableFormats, downloadFilename, FORMAT_META, shareUrl } from "./exportFormats";
import { DEFAULT_CLOSING, sliceSummary, USB_TETHER_WARNING } from "./printSummary";
import {
  handshake,
  isUsbPrintingSupported,
  type PrinterPort,
  requestPrinterPort,
  streamJob,
} from "./usbPrinter";

async function fetchAndSave(url: string, filename: string, init?: RequestInit): Promise<void> {
  const res = await fetch(url, init);
  // The status text as well as the number: this message is shown to someone,
  // and a toast body reading only "409" tells them nothing they can act on.
  if (!res.ok) throw new Error(res.statusText ? `${res.status} ${res.statusText}` : `${res.status}`);
  const blob = await res.blob();
  const objectUrl = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = objectUrl;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(objectUrl);
}

/** The same bytes as the download, as text rather than as a file.
 *
 * The USB path needs the job in hand to send it line by line, so it reads the
 * body instead of handing it to the browser's downloader. Same URL, same
 * header, same server route — only the destination differs.
 */
async function fetchGcode(url: string): Promise<string> {
  const res = await fetch(url, { headers: printHeaders() });
  if (!res.ok) throw new Error(res.statusText ? `${res.status} ${res.statusText}` : `${res.status}`);
  return res.text();
}

/** Something the reader has to go and do before printing can work.
 *
 * Its own dialog rather than a toast: both cases are an instruction with a
 * place to go, and a message that dismisses itself is the wrong shape for one. */
type Notice = { title: string; body: string } | null;

/** A slice, carrying the version it is a slice *of* and what can be done with it.
 *
 * The id travels with the numbers because the two must not come apart. This
 * component is not remounted when the active version changes, and that change
 * does not need a click: a chat or generation turn finishing re-reads the
 * project and moves the active version from an SSE event. Reading `version.id`
 * again at send time would then print whatever became current while the dialog
 * was open, under a summary describing something else.
 *
 * The capability travels with it for the same reason — the dialog must offer
 * what was true when the slice was made, not what a later poll says.
 */
type Sliced = { versionId: number; result: SliceResult; can: PrintCapability } | null;

/** A print leaving through this tab's own USB connection.
 *
 * In state rather than in a ref because this dialog is the only thing telling
 * the reader a print is running, and it has to re-render as the count moves.
 * The controller travels with it so Stop reaches the stream that is actually
 * running rather than one started after it.
 */
type UsbJob = {
  phase: "connecting" | "printing";
  sent: number;
  total: number;
  controller: AbortController;
} | null;

/** What the dialog says happens after the numbers.
 *
 * Three cases, and telling them apart matters because two of them are the
 * reader's to fix and one is not. Somebody running this on their own machine
 * who has not set an address yet was being told the installation cannot reach a
 * printer, which is false and offers them nothing to do about it.
 */
export function closingFor(can: PrintCapability): string {
  if (can.can_send) return DEFAULT_CLOSING;
  if (can.mode === "auto" && !can.printer_configured && can.can_configure) {
    return "Add your printer's address in Settings to send jobs straight to it.";
  }
  return "This installation cannot reach a printer, so take the file to yours.";
}

export function ExportShare({ version }: { version: Version }) {
  const toast = useToast();
  const [busy, setBusy] = useState<ArtifactKind | null>(null);
  const [printStep, setPrintStep] = useState<"" | "slicing" | "sending" | "saving">("");
  const [sliced, setSliced] = useState<Sliced>(null);
  const [notice, setNotice] = useState<Notice>(null);
  const [usb, setUsb] = useState<UsbJob>(null);
  // True only while the device chooser is up. The dialog stays open behind
  // it -- dismissing the chooser has to leave something to try again from --
  // so without this a second click opens a second chooser and races a second
  // handshake at the same port.
  const [choosing, setChoosing] = useState(false);
  const formats = availableFormats(version);
  if (formats.length === 0) return null;
  const printable = formats.includes("stl");

  // Whether this browser can talk to a USB device at all. A browser fact rather
  // than a server one, so it is read here and never asked of `PrintCapability`
  // — the deployment has no way to know and no business deciding.
  const usbAvailable = isUsbPrintingSupported();
  // Web Serial is necessary and not sufficient: this streams the same bytes the
  // download saves, from the same route, so a deployment that will not serve
  // the G-code cannot be printed from either.
  const usbOffered = usbAvailable && sliced != null && sliced.can.can_download;
  // Where the deployment cannot reach a printer itself, USB is the only offer
  // that actually prints something, so it takes the emphasis the download had.
  const usbIsBest = usbOffered && sliced != null && !sliced.can.can_send;

  async function download(kind: ArtifactKind) {
    setBusy(kind);
    try {
      await fetchAndSave(artifactUrl(version.id, kind), downloadFilename(version.id, kind));
      toast.success(`${FORMAT_META[kind].label} downloaded`);
    } catch {
      toast.error(`Couldn't download ${FORMAT_META[kind].label}`, "the artifact may be unavailable");
    } finally {
      setBusy(null);
    }
  }

  /** Slice, then ask. Nothing reaches the printer from this half.
   *
   * What can be known cheaply is checked first: slicing a model for two minutes
   * only to report that this deployment cannot print at all wastes the reader's
   * time on a question that could have been asked immediately. A missing
   * printer address is *not* one of those cases any more — the job can still be
   * downloaded, which is the only thing a deployment in a datacentre could ever
   * have offered. */
  async function print() {
    const target = version.id;
    setPrintStep("slicing");
    try {
      const capability = await fetchPrintCapability();
      if (!capability.can_send && !capability.can_download) {
        // The mode first: a build with printing switched off *and* no slicer
        // would otherwise be told to install one, which would not help.
        setNotice(
          capability.mode === "off"
            ? { title: "Printing is off", body: "This installation has printing switched off." }
            : { title: "No slicer yet", body: capability.slicer_hint },
        );
        return;
      }

      const result = await sliceVersion(target);
      if (result.slicer_missing) {
        setNotice({ title: "No slicer yet", body: result.detail });
        return;
      }
      if (!result.ok) {
        toast.error("Couldn't prepare this model", result.detail);
        return;
      }
      setSliced({ versionId: target, result, can: capability });
    } catch (err) {
      toast.error("Couldn't prepare this model", errMessage(err));
    } finally {
      setPrintStep("");
    }
  }

  /** The half that commits material. Only reachable through the dialog.
   *
   * Sends the version the dialog described, not whichever is active now. */
  async function confirmSend() {
    if (!sliced) return;
    const target = sliced.versionId;
    setSliced(null);
    setPrintStep("sending");
    try {
      const result = await sendVersionToPrinter(target);
      if (result.ok) toast.success("Sent to the printer", "Check the printer to start the job.");
      else toast.error("Couldn't reach the printer", result.detail);
    } catch (err) {
      toast.error("Couldn't reach the printer", errMessage(err));
    } finally {
      setPrintStep("");
    }
  }

  /** Hand the job over as a file, for a printer this machine cannot reach. */
  async function confirmDownload() {
    if (!sliced) return;
    const target = sliced.versionId;
    setSliced(null);
    setPrintStep("saving");
    try {
      await fetchAndSave(gcodeUrl(target), `model_${target}.gcode`, { headers: printHeaders() });
      toast.success("G-code downloaded", "Send it to your printer the way you normally would.");
    } catch (err) {
      toast.error("Couldn't save the G-code", errMessage(err));
    } finally {
      setPrintStep("");
    }
  }

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
  async function confirmUsbPrint() {
    if (!sliced || choosing) return;
    const target = sliced.versionId;

    let port: PrinterPort;
    setChoosing(true);
    try {
      port = await requestPrinterPort();
    } catch {
      // The chooser was dismissed, or held nothing to choose. That is an answer
      // rather than a fault, so the dialog stays open behind it.
      return;
    } finally {
      setChoosing(false);
    }

    setSliced(null);
    const controller = new AbortController();
    setUsb({ phase: "connecting", sent: 0, total: 0, controller });
    try {
      const shake = await handshake(port);
      if (!shake.ok || !shake.baudRate) {
        setNotice({
          title: "That is not a printer",
          body: shake.detail ?? "Nothing on that port answered like a printer.",
        });
        return;
      }

      const gcode = await fetchGcode(gcodeUrl(target));
      setUsb({ phase: "printing", sent: 0, total: 0, controller });

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
          setUsb({ phase: "printing", sent, total, controller });
        },
      });

      if (result.ok) toast.success("Printed over USB", result.detail);
      else if (result.stopped) toast.success("Print stopped", result.detail);
      else toast.error("The print stopped", result.detail);
    } catch (err) {
      toast.error("The print stopped", errMessage(err));
    } finally {
      setUsb(null);
    }
  }

  function share() {
    const url = shareUrl(location.origin, BASE_URL, version.project_id, version.id);
    navigator.clipboard
      .writeText(url)
      .then(() => toast.success("Share link copied"))
      .catch(() => toast.error("Couldn't copy link"));
  }

  const printLabel =
    printStep === "slicing"
      ? "Preparing…"
      : printStep === "sending"
        ? "Sending…"
        : printStep === "saving"
          ? "Saving…"
          : "⎙ Print";

  return (
    <div className="export">
      <div className="export-formats">
        {formats.map((kind) => (
          <Tooltip key={kind} label={FORMAT_META[kind].desc}>
            <button
              className="export-chip"
              disabled={busy != null}
              onClick={() => download(kind)}
            >
              {busy === kind ? "…" : FORMAT_META[kind].label}
            </button>
          </Tooltip>
        ))}
      </div>
      <div className="export-actions">
        {printable && (
          <Tooltip label="Slice this model for your 3D printer">
            <Button size="sm" variant="ghost" disabled={printStep !== ""} onClick={print}>
              {printLabel}
            </Button>
          </Tooltip>
        )}
        <Button size="sm" variant="ghost" onClick={share}>
          ↗ Share
        </Button>
      </div>

      <Modal
        open={sliced != null}
        onOpenChange={(open) => !open && setSliced(null)}
        title={sliced?.can.can_send ? "Send this to the printer?" : "Ready to print"}
        description={
          sliced ? (
            <>
              {sliceSummary(sliced.result.stats, closingFor(sliced.can))}
              {/* Stated here rather than after the click, because keeping a tab
                  open for the length of a print is the cost being weighed
                  against the walk to the printer — and only the USB option
                  carries it. */}
              {usbOffered && <span className="print-tether"> Over USB: {USB_TETHER_WARNING}</span>}
            </>
          ) : (
            ""
          )
        }
      >
        <div className="modal-footer">
          <Button type="button" variant="ghost" onClick={() => setSliced(null)}>
            Cancel
          </Button>
          {sliced?.can.can_download && (
            <Button
              type="button"
              variant={sliced?.can.can_send || usbIsBest ? "ghost" : "primary"}
              onClick={confirmDownload}
            >
              Download G-code
            </Button>
          )}
          {usbOffered && (
            <Button
              type="button"
              variant={usbIsBest ? "primary" : "ghost"}
              disabled={choosing}
              onClick={confirmUsbPrint}
            >
              Print over USB
            </Button>
          )}
          {sliced?.can.can_send && (
            <Button type="button" variant="primary" onClick={confirmSend}>
              Send to printer
            </Button>
          )}
        </div>
      </Modal>

      <Modal
        open={usb != null}
        /* Deliberately not dismissable. Esc and a click outside both arrive
           here, and either one closing this would leave a print running with
           nothing on screen saying so — and no way back to the Stop button.
           Ending a print is what Stop is for. */
        onOpenChange={() => undefined}
        title="Printing over USB"
        description={
          usb?.phase === "connecting"
            ? "Looking for a printer on that port…"
            : `${usb?.sent ?? 0} of ${usb?.total ?? 0} lines sent. ${USB_TETHER_WARNING}`
        }
      >
        <div className="modal-footer">
          <Button
            type="button"
            variant="danger"
            // Nothing has been sent yet while the handshake runs, and that step
            // is bounded by its own timeouts rather than by the job's length.
            disabled={usb?.phase !== "printing"}
            onClick={() => usb?.controller.abort()}
          >
            Stop
          </Button>
        </div>
      </Modal>

      <Modal
        open={notice != null}
        onOpenChange={(open) => !open && setNotice(null)}
        title={notice?.title ?? ""}
        description={notice?.body ?? ""}
      >
        <div className="modal-footer">
          <Button type="button" variant="primary" onClick={() => setNotice(null)}>
            Got it
          </Button>
        </div>
      </Modal>
    </div>
  );
}
