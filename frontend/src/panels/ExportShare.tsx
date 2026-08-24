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
import { sliceSummary } from "./printSummary";

async function fetchAndSave(url: string, filename: string, init?: RequestInit): Promise<void> {
  const res = await fetch(url, init);
  if (!res.ok) throw new Error(`${res.status}`);
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

export function ExportShare({ version }: { version: Version }) {
  const toast = useToast();
  const [busy, setBusy] = useState<ArtifactKind | null>(null);
  const [printStep, setPrintStep] = useState<"" | "slicing" | "sending" | "saving">("");
  const [sliced, setSliced] = useState<Sliced>(null);
  const [notice, setNotice] = useState<Notice>(null);
  const formats = availableFormats(version);
  if (formats.length === 0) return null;
  const printable = formats.includes("stl");

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
        setNotice(
          capability.slicer_available
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
          sliceSummary(sliced?.result.stats) +
          (sliced && !sliced.can.can_send
            ? " This installation cannot reach a printer, so take the file over yourself."
            : "")
        }
      >
        <div className="modal-footer">
          <Button type="button" variant="ghost" onClick={() => setSliced(null)}>
            Cancel
          </Button>
          {sliced?.can.can_download && (
            <Button
              type="button"
              variant={sliced?.can.can_send ? "ghost" : "primary"}
              onClick={confirmDownload}
            >
              Download G-code
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
