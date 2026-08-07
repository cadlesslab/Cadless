/** Export format picker + share link. */
import { useState } from "react";

import { artifactUrl, type ArtifactKind, type Version } from "../api";
import { Button, Tooltip, useToast } from "../components";
import { BASE_URL } from "../routing";
import { availableFormats, downloadFilename, FORMAT_META, shareUrl } from "./exportFormats";

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

export function ExportShare({ version }: { version: Version }) {
  const toast = useToast();
  const [busy, setBusy] = useState<ArtifactKind | null>(null);
  const formats = availableFormats(version);
  if (formats.length === 0) return null;

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

  function share() {
    const url = shareUrl(location.origin, BASE_URL, version.project_id, version.id);
    navigator.clipboard
      .writeText(url)
      .then(() => toast.success("Share link copied"))
      .catch(() => toast.error("Couldn't copy link"));
  }

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
      <Button size="sm" variant="ghost" onClick={share}>
        ↗ Share
      </Button>
    </div>
  );
}
