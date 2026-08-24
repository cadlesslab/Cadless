/** Export format picker + share link + print. */
import { useState } from "react";

import {
  artifactUrl,
  type ArtifactKind,
  fetchPrintCapability,
  sendVersionToPrinter,
  type SliceResult,
  sliceVersion,
  type Version,
} from "../api";
import { Button, ConfirmDialog, Modal, Tooltip, useToast } from "../components";
import { errMessage } from "../errors";
import { BASE_URL } from "../routing";
import { availableFormats, downloadFilename, FORMAT_META, shareUrl } from "./exportFormats";
import { sliceSummary } from "./printSummary";

async function fetchAndSave(url: string, filename: string): Promise<void> {
  const res = await fetch(url);
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

/** A slice, carrying the version it is a slice *of*.
 *
 * The id travels with the numbers because the two must not come apart. This
 * component is not remounted when the active version changes, and that change
 * does not need a click: a chat or generation turn finishing re-reads the
 * project and moves the active version from an SSE event. Reading `version.id`
 * again at send time would then print whatever became current while the dialog
 * was open, under a summary describing something else.
 */
type Sliced = { versionId: number; result: SliceResult } | null;

export function ExportShare({ version }: { version: Version }) {
  const toast = useToast();
  const [busy, setBusy] = useState<ArtifactKind | null>(null);
  const [printStep, setPrintStep] = useState<"" | "slicing" | "sending">("");
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
   * only to report that no address was ever set wastes the reader's time on a
   * question that could have been asked immediately. */
  async function print() {
    // Read once, at the moment the user asked. Everything below belongs to
    // this version even if the active one moves while slicing runs.
    const target = version.id;
    setPrintStep("slicing");
    try {
      const capability = await fetchPrintCapability();
      if (!capability.printer_configured) {
        setNotice({
          title: "No printer yet",
          body: "Add your printer's address in Settings, then press Print again. It has to be a printer on your own network.",
        });
        return;
      }
      if (!capability.slicer_available) {
        setNotice({ title: "No slicer yet", body: capability.slicer_hint });
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
      setSliced({ versionId: target, result });
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

  function share() {
    const url = shareUrl(location.origin, BASE_URL, version.project_id, version.id);
    navigator.clipboard
      .writeText(url)
      .then(() => toast.success("Share link copied"))
      .catch(() => toast.error("Couldn't copy link"));
  }

  const printLabel =
    printStep === "slicing" ? "Preparing…" : printStep === "sending" ? "Sending…" : "⎙ Print";

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
          <Tooltip label="Slice this model and send it to your 3D printer">
            <Button size="sm" variant="ghost" disabled={printStep !== ""} onClick={print}>
              {printLabel}
            </Button>
          </Tooltip>
        )}
        <Button size="sm" variant="ghost" onClick={share}>
          ↗ Share
        </Button>
      </div>

      <ConfirmDialog
        open={sliced != null}
        title="Send this to the printer?"
        message={sliceSummary(sliced?.result.stats)}
        confirmLabel="Send to printer"
        destructive={false}
        onConfirm={confirmSend}
        onClose={() => setSliced(null)}
      />

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
