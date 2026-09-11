/** Export format picker + share link + print. */
import { useEffect, useRef, useState } from "react";

import {
  artifactUrl,
  type ArtifactKind,
  fetchFilamentLevel,
  type ScaleOffer,
  type FilamentLevel,
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
import { fetchAndSave } from "./download";
import {
  availableFormats,
  chipLabel,
  downloadFilename,
  FORMAT_META,
  partsOf,
  shareUrl,
} from "./exportFormats";
import {
  DEFAULT_CLOSING,
  filamentNote,
  SCALED_MODEL_WARNING,
  sliceSummary,
  USB_TETHER_WARNING,
} from "./printSummary";
import { isUsbPrintingSupported } from "./usbPrinter";
import { useUsbPrint } from "./useUsbPrint";

/** Something the reader has to go and do before printing can work.
 *
 * Its own dialog rather than a toast: both cases are an instruction with a
 * place to go, and a message that dismisses itself is the wrong shape for one. */
type Notice = { title: string; body: string } | null;

/** A model that will not fit, and what could be printed instead.
 *
 * Its own state rather than a `Notice`, because it is a question with two
 * answers rather than something to acknowledge. The version id travels with it
 * for the reason the sliced numbers carry theirs: the active version moves
 * without a click, and answering yes must scale the model that was measured.
 */
type TooBig = { versionId: number; detail: string; offer: ScaleOffer } | null;

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
type Sliced = {
  versionId: number;
  result: SliceResult;
  can: PrintCapability;
  /** What the machine said it had left when this was sliced, or null when it
   * had nothing to say. Carried with the slice for the same reason the
   * capability is: the dialog reports what was true when the numbers were made,
   * not what a later poll says. */
  level: FilamentLevel | null;
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
    // "below", not "in Settings": the address moved here, and this dialog opens
    // over the panel that now holds it. Sending somebody to Settings would be
    // sending them past the box they are looking for.
    return "Add your printer's address below to send jobs straight to it.";
  }
  return "This installation cannot reach a printer, so take the file to yours.";
}

export function ExportShare({ version }: { version: Version }) {
  const toast = useToast();
  const [busy, setBusy] = useState<ArtifactKind | null>(null);
  const [printStep, setPrintStep] = useState<"" | "slicing" | "sending" | "saving">("");
  const [sliced, setSliced] = useState<Sliced>(null);
  /** What `sliced` is *now*, readable from inside a handler that has awaited.
   *
   * `confirmUsbPrint` is a closure built during a render, and it awaits the
   * device chooser. Reading the state variable after that await answers with
   * what was true when the button was pressed, not with what is true when the
   * chooser comes back — so a dialog dismissed in between looked open, and the
   * print started anyway. A ref is the same object across renders, so writing
   * to it is visible through a closure that captured it earlier.
   */
  const slicedNow = useRef<Sliced>(null);
  useEffect(() => {
    slicedNow.current = sliced;
  }, [sliced]);
  const [notice, setNotice] = useState<Notice>(null);
  const [tooBig, setTooBig] = useState<TooBig>(null);
  // The USB flow keeps its own state: a chooser, a handshake and a stream are a
  // flow rather than a handler, and none of it is about offering formats.
  // `slicedNow` is what it reads to find out whether this dialog is still the
  // one that was open when the chooser went up.
  const { job: usb, choosing, start: startUsbPrint } = useUsbPrint(slicedNow, () =>
    setSliced(null),
  );
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
  // Computed once: the dialog renders it, and calling it twice to ask whether
  // there is anything to render would ask the same question twice.
  const filament = sliced
    ? filamentNote(sliced.level, sliced.result.stats?.filament_grams)
    : "";

  async function download(kind: ArtifactKind) {
    setBusy(kind);
    try {
      const pieces = partsOf(version, kind);
      if (pieces.length > 1) {
        // One click, one model, every file of it. Saving only the first piece
        // would hand back a third of a shelf and say it had succeeded, and the
        // reader has no way to notice the rest never arrived. Sequential rather
        // than parallel so the browser's own save prompts stay in order.
        for (const piece of pieces) {
          await fetchAndSave(
            artifactUrl(version.id, kind, piece.part),
            downloadFilename(version.id, kind, piece.part),
          );
        }
        toast.success(`${FORMAT_META[kind].label} downloaded`, `${pieces.length} pieces`);
      } else {
        await fetchAndSave(artifactUrl(version.id, kind), downloadFilename(version.id, kind));
        toast.success(`${FORMAT_META[kind].label} downloaded`);
      }
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
  /** Whether this deployment can do anything at all with a sliced job.
   *
   * Checked before slicing, because slicing a model for two minutes only to
   * report that this deployment cannot print wastes the reader's time on a
   * question that could have been asked immediately. The mode comes first: a
   * build with printing switched off *and* no slicer would otherwise be told to
   * install one, which would not help. */
  function cannotPrint(capability: PrintCapability): boolean {
    if (capability.can_send || capability.can_download) return false;
    setNotice(
      capability.mode === "off"
        ? { title: "Printing is off", body: "This installation has printing switched off." }
        : { title: "No slicer yet", body: capability.slicer_hint },
    );
    return true;
  }

  /** What to do with a slice result, wherever it came from.
   *
   * Both the first call and the scaled second one land here, because both meet
   * the same three answers: no slicer, a refusal, a job. Written once so the
   * second cannot quietly keep a shorter list than the first — it had one, and
   * a slicer that went missing between the two calls came back as "couldn't
   * prepare this model" with the install hint as the body. */
  function settle(
    target: number,
    result: SliceResult,
    capability: PrintCapability,
    level: FilamentLevel | null,
  ) {
    if (result.slicer_missing) {
      setNotice({ title: "No slicer yet", body: result.detail });
      return;
    }
    if (!result.ok) {
      // A refusal that has something to offer is a question rather than a dead
      // end. Most of the catalogue is furniture at real scale, so for that half
      // "it will not fit" was always the last word.
      if (result.scale_offer) {
        setTooBig({ versionId: target, detail: result.detail, offer: result.scale_offer });
        return;
      }
      toast.error("Couldn't prepare this model", result.detail);
      return;
    }
    setSliced({ versionId: target, result, can: capability, level });
  }

  async function print() {
    const target = version.id;
    setPrintStep("slicing");
    try {
      const capability = await fetchPrintCapability();
      if (cannotPrint(capability)) return;

      // Asked alongside the slice rather than before it. Slicing is the slow
      // step, so the round trip to the printer costs nothing on the clock — and
      // a printer that is off, absent or has no cartridge answers `ok: false`
      // rather than throwing, so it cannot stop a print being offered.
      const [result, level] = await Promise.all([
        sliceVersion(target),
        fetchFilamentLevel().catch(() => null),
      ]);
      settle(target, result, capability, level);
    } catch (err) {
      toast.error("Couldn't prepare this model", errMessage(err));
    } finally {
      setPrintStep("");
    }
  }

  /** Slice it again, small enough to fit, because somebody said to.
   *
   * A second call rather than a flag carried from the first, so the model is
   * only ever scaled as the answer to a question that was asked. That is kept
   * here, by there being no other place the flag is set; the route will accept
   * it on a first call, and declines to scale a model that fits regardless.
   */
  async function confirmScaled() {
    if (!tooBig) return;
    const target = tooBig.versionId;
    setTooBig(null);
    setPrintStep("slicing");
    try {
      const capability = await fetchPrintCapability();
      if (cannotPrint(capability)) return;
      const [result, level] = await Promise.all([
        sliceVersion(target, { scaleToFit: true }),
        fetchFilamentLevel().catch(() => null),
      ]);
      settle(target, result, capability, level);
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

  /** Hand the print to the USB flow, and show what it hands back.
   *
   * The whole of the streaming lives in the hook; what belongs here is the one
   * thing the panel owns — a dialog for something the reader has to go and do,
   * which is what "that port is not a printer" is.
   */
  async function confirmUsbPrint() {
    if (!sliced) return;
    const said = await startUsbPrint(sliced.versionId);
    if (said) setNotice(said);
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
              {busy === kind ? "…" : chipLabel(version, kind)}
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
              {/* That this is not the model as it was drawn. The figures above
                  are the slicer's own and describe the scaled object without
                  ever saying it is one, and the decision to scale was made a
                  dialog ago — so the last screen before the material is
                  committed says which model this is. */}
              {sliced.result.scaled && (
                <span className="print-scaled"> Scaled down to fit the bed.</span>
              )}
              {/* What the machine has left, next to what this will take. The
                  pair is the whole question somebody is answering here. */}
              {filament && <span className="print-filament"> {filament}</span>}
              {/* What the slicer said while still producing a job. It matters
                  most on a scaled model, where the tool proposed the shape that
                  is now hard to print. */}
              {sliced.result.warning && (
                <span className="print-slicer-warning"> {sliced.result.warning}</span>
              )}
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
            ? "Looking for a printer on that port… this can take a few seconds."
            : `${usb?.sent ?? 0} of ${usb?.total ?? 0} lines sent. ${USB_TETHER_WARNING}`
        }
      >
        <div className="modal-footer">
          <Button
            type="button"
            variant="danger"
            // Enabled in both phases. Connecting can take four baud candidates
            // and a port open apiece, and a disabled Stop over a dialog that
            // cannot be dismissed leaves no way out of it at all.
            onClick={() => usb?.controller.abort()}
          >
            Stop
          </Button>
        </div>
      </Modal>

      <Modal
        open={tooBig != null}
        onOpenChange={(open) => !open && setTooBig(null)}
        title="Too big for this printer"
        description={
          tooBig ? (
            <>
              {tooBig.detail}
              <span className="print-scale">
                {" "}
                Scaled to about {tooBig.offer.percent}% it would be{" "}
                {tooBig.offer.size.join(" × ")} mm.
              </span>
              {/* Before the decision, not after: what comes out is a model of
                  the thing rather than a smaller one of it, and only the reader
                  knows which of those they wanted. */}
              <span className="print-scale-warning"> {SCALED_MODEL_WARNING}</span>
            </>
          ) : (
            ""
          )
        }
      >
        <div className="modal-footer">
          <Button type="button" variant="ghost" onClick={() => setTooBig(null)}>
            Cancel
          </Button>
          <Button type="button" variant="primary" onClick={confirmScaled}>
            Scale it and print
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
