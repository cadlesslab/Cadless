/** Taking in a `.cls` that was handed over directly.
 *
 * Its own module rather than a section of a panel that fetches from a service,
 * because it is not part of one: it needs no account, reaches no server but
 * this one, and is the only way to open a package in a build that talks
 * nowhere else. Written into such a panel, it was a feature that could only be
 * removed along with it.
 *
 * Separated by rendering as well as by path: it has a rail entry of its own in
 * `builtins.tsx`. A build assembled with no remote catalogue at all still
 * offers this, and taking one away carries nothing else off with it.
 */
import { useState } from "react";

import * as api from "../api";
import type { ImportResult } from "../api";
import { Button, HelpPopover, Panel, TextInput, useToast } from "../components";
import { errMessage } from "../errors";

/** A file's size in the units someone would say it in.
 *
 * Rounded rather than exact: this is here so a package that is plainly the
 * wrong one can be spotted before it is imported, and a byte count answers a
 * question nobody was asking. */
function fileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} ${bytes === 1 ? "byte" : "bytes"}`;
  // Rounded before it is compared, not after: a size that rounds up to 1024 of
  // something is one of the next thing along, and saying "1024 KB" is how a
  // unit table gives itself away.
  const kb = Math.round(bytes / 1024);
  return kb < 1024 ? `${kb} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/** Where a package someone sent is checked and taken in.
 *
 * Not behind any sign-in, on purpose. A `.cls` handed over directly — on a
 * drive, in a message — went through no upload check anywhere, so it is the
 * delivery with nothing at all in front of it, and it is this machine that runs
 * the code inside. Requiring an account to check it would turn away the one
 * case that needs checking most. */
export function ImportPanel() {
  const toast = useToast();
  const [file, setFile] = useState<File | null>(null);
  // Whether the fold holding the fingerprint is open.
  const [advanced, setAdvanced] = useState(false);
  // Kept here rather than inside the disclosure so closing the fold does not
  // silently drop a digest that was already typed in.
  const [fingerprint, setFingerprint] = useState("");
  const [busy, setBusy] = useState(false);
  const [imported, setImported] = useState<ImportResult | null>(null);

  async function onImport() {
    if (!file) return;
    setBusy(true);
    try {
      setImported(await api.importCatalog(file, fingerprint.trim() || undefined));
    } catch (err) {
      // The refusal names the file and the reason. Reducing it to "could not
      // import" would leave someone with a package and no idea what is wrong
      // with it.
      setImported(null);
      toast.error("Could not import this package", errMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    /* The card is `Panel`'s, like every other entry on the rail. It used to be
       a section inside another panel, which is what drew the card around it; on
       a rail entry of its own there was nothing to, and it came out as bare
       text over the viewport.
       The explanation sits in the header's actions slot rather than beside a
       heading of its own — `Panel` owns the title now, and two headings for one
       panel is one more than it has. */
    <Panel
      title="Import a package"
      className="import"
      actions={
        <HelpPopover label="About importing a package" title="Importing a package">
          For a <code>.cls</code> file you already have. Nothing here needs an account — a package
          handed over directly went through no upload check anywhere, which is exactly why it is
          checked on the way in.
        </HelpPopover>
      }
    >
      <label htmlFor="import-file">Package file</label>
      <TextInput
        id="import-file"
        type="file"
        accept=".cls"
        onChange={(e) => {
          setFile(e.target.files?.[0] ?? null);
          setImported(null);
        }}
      />
      {/* The file control states the name in a font nobody chose and drops it
          on the floor at the width of a flyout. Said again here, because
          importing the wrong package is otherwise silent until it lands. */}
      {file && (
        <p className="import-chosen">
          {file.name} · {fileSize(file.size)}
        </p>
      )}
      {/* Folded rather than gone: almost nobody has a fingerprint, so it does
          not earn a line of its own — but the person who was sent one is
          exactly the person who should be able to use it, and it is the only
          thing that notices an edit made after publishing. Its own fold, not
          the explanation's: a field is something to fill in, and the card that
          explains this section floats away when it is clicked past. */}
      <button
        type="button"
        className="import-advanced"
        aria-expanded={advanced}
        aria-controls="import-advanced-fold"
        onClick={() => setAdvanced((shown) => !shown)}
      >
        Advanced
      </button>
      {advanced && (
        <div id="import-advanced-fold" className="import-field">
          <div className="import-head">
            <label htmlFor="import-digest">Fingerprint (optional)</label>
            <HelpPopover label="About the fingerprint" title="Fingerprint">
              If whoever sent this also sent its fingerprint, put it here — it is what notices an
              edit made since they published it.
            </HelpPopover>
          </div>
          <TextInput
            id="import-digest"
            value={fingerprint}
            onChange={(e) => setFingerprint(e.target.value)}
          />
        </div>
      )}
      <Button onClick={onImport} disabled={!file || busy}>
        Import
      </Button>

      {imported && (
        <div className="import-result">
          <p>Imported {imported.name}.</p>
          <p className="hint">
            {imported.steps_checked} {imported.steps_checked === 1 ? "step" : "steps"} passed the
            code check before anything was written to disk.
          </p>
          {imported.digest_confirmed ? (
            <p className="hint">
              It matches the fingerprint you gave, so nothing has been changed since it was
              published.
            </p>
          ) : (
            // Said rather than left out: the code check that did run is not the
            // same assurance, and silence here would let one pass for the other.
            <p className="hint">
              It was read and its code was checked, but nothing was offered to check it against —
              so there is nothing to prove it is the package its author sent.
            </p>
          )}
          {imported.project_id === null && (
            <p className="hint">This item was already here and unchanged, so it was left as it is.</p>
          )}
        </div>
      )}
    </Panel>
  );
}
