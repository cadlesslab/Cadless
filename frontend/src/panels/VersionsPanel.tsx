/** Version history: thumbnails, lineage, rename/annotate, compare,
 * prompt recall. Selecting a version loads it; rerun re-executes its code. */
import { useState } from "react";

import type { Version } from "../api";
import { Button, EmptyState, IconButton, Modal, Panel, PromptDialog, Tooltip } from "../components";
import { useStoreSelector } from "../state";
import { useApp } from "../useApp";
import { getNote, setNote, useNote } from "./annotations";
import { useThumbnail } from "./thumbnails";
import { compareMetrics, versionLabel } from "./versionUtils";

function Thumb({ id, ok }: { id: number; ok: boolean }) {
  const src = useThumbnail(id);
  return (
    <span className="ver-thumb">
      {src ? <img src={src} alt="" /> : <span className={`ver-thumb-ph ${ok ? "" : "bad"}`}>{ok ? "◳" : "⚠"}</span>}
    </span>
  );
}

function VersionCard({
  v,
  active,
  current,
  selectedForCompare,
  rerunnable,
  onSelect,
  onRerun,
  onRename,
  onRecall,
  onBranch,
  onToggleCompare,
}: {
  v: Version;
  active: boolean;
  current: boolean;
  selectedForCompare: boolean;
  rerunnable: boolean;
  onSelect: () => void;
  onRerun: () => void;
  onRename: () => void;
  onRecall: () => void;
  onBranch: () => void;
  onToggleCompare: () => void;
}) {
  const note = useNote(v.id);
  return (
    <li className={`ver-card ${active ? "active" : ""}`}>
      <button className="ver-card-main" onClick={onSelect} aria-current={active}>
        <Thumb id={v.id} ok={v.ok} />
        <span className="ver-card-body">
          <span className="ver-card-top">
            <span className={v.ok ? "ok" : "bad"}>v{v.id}</span>
            {current && <span className="badge">current</span>}
            {v.parent_version_id != null && (
              <span className="ver-lineage" title={`refined from v${v.parent_version_id}`}>
                ↳ v{v.parent_version_id}
              </span>
            )}
            {v.plan_step != null && (
              <span className="ver-step" title={`written at plan step ${v.plan_step}`}>
                step {v.plan_step}
              </span>
            )}
          </span>
          <span className="ver-card-label" title={v.prompt}>
            {versionLabel(v, note)}
          </span>
        </span>
      </button>
      <span className="ver-card-actions">
        <Tooltip label="Compare">
          <input
            type="checkbox"
            aria-label={`Compare v${v.id}`}
            checked={selectedForCompare}
            onChange={onToggleCompare}
          />
        </Tooltip>
        <IconButton label={`Use v${v.id} prompt`} onClick={onRecall}>↩</IconButton>
        <IconButton label={`Branch from v${v.id}`} onClick={onBranch}>⑂</IconButton>
        <IconButton label={`Rename v${v.id}`} onClick={onRename}>✎</IconButton>
        {/* Catalog items are read-only: re-running would overwrite baked
            artifacts at the wrong scale (#31), and the backend refuses it. */}
        {rerunnable && (
          <IconButton label={`Re-run v${v.id}`} onClick={onRerun}>↻</IconButton>
        )}
      </span>
    </li>
  );
}

function CompareDialog({ a, b, onClose }: { a: Version; b: Version; onClose: () => void }) {
  const rows = compareMetrics(a, b);
  const fmt = (n: number | null) => (n == null ? "—" : n.toFixed(1));
  return (
    <Modal open onOpenChange={(o) => !o && onClose()} title={`Compare v${a.id} vs v${b.id}`}>
      <table className="compare-table">
        <thead>
          <tr>
            <th></th>
            <th>v{a.id}</th>
            <th>v{b.id}</th>
            <th>Δ</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.label}>
              <td>{r.label}</td>
              <td>{fmt(r.a)}</td>
              <td>{fmt(r.b)}</td>
              <td className={r.delta != null && r.delta !== 0 ? (r.delta > 0 ? "ok" : "bad") : ""}>
                {r.delta == null ? "—" : `${r.delta > 0 ? "+" : ""}${r.delta.toFixed(1)}`}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="modal-footer">
        <Button variant="primary" onClick={onClose}>Close</Button>
      </div>
    </Modal>
  );
}

export function VersionsPanel() {
  const app = useApp();
  const versions = useStoreSelector((s) => s.versions);
  const activeVersionId = useStoreSelector((s) => s.activeVersionId);
  const currentVersionId = useStoreSelector(
    (s) => s.projects.find((p) => p.id === s.activeProjectId)?.current_version_id ?? null,
  );
  const isCatalog = useStoreSelector(
    (s) => s.projects.find((p) => p.id === s.activeProjectId)?.is_catalog ?? false,
  );
  const [renaming, setRenaming] = useState<Version | null>(null);
  const [compareIds, setCompareIds] = useState<number[]>([]);
  const [comparing, setComparing] = useState(false);

  const ordered = [...versions].reverse(); // newest first
  const byId = (id: number) => versions.find((v) => v.id === id);
  const comparePair =
    compareIds.length === 2 ? ([byId(compareIds[0]), byId(compareIds[1])] as const) : null;

  function toggleCompare(id: number) {
    setCompareIds((ids) =>
      ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id].slice(-2),
    );
  }

  return (
    <Panel
      title="History"
      actions={
        <Button
          size="sm"
          disabled={compareIds.length !== 2}
          onClick={() => setComparing(true)}
        >
          Compare
        </Button>
      }
    >
      {ordered.length === 0 ? (
        <EmptyState>No versions yet.</EmptyState>
      ) : (
        <ul className="ver-cards">
          {ordered.map((v) => (
            <VersionCard
              key={v.id}
              v={v}
              active={v.id === activeVersionId}
              current={v.id === currentVersionId}
              selectedForCompare={compareIds.includes(v.id)}
              rerunnable={!isCatalog}
              onSelect={() => app.showVersion(v.id)}
              onRerun={() => app.rerunVersion(v.id)}
              onRename={() => setRenaming(v)}
              onRecall={() => app.recallPrompt(v.prompt)}
              onBranch={() => app.branchFrom(v.id)}
              onToggleCompare={() => toggleCompare(v.id)}
            />
          ))}
        </ul>
      )}

      {renaming && (
        <PromptDialog
          open
          title={`Rename v${renaming.id}`}
          label="Label"
          initialValue={getNote(renaming.id) ?? renaming.prompt}
          confirmLabel="Save"
          onSubmit={(name) => {
            setNote(renaming.id, name);
            setRenaming(null);
          }}
          onClose={() => setRenaming(null)}
        />
      )}

      {comparing && comparePair && comparePair[0] && comparePair[1] && (
        <CompareDialog a={comparePair[0]} b={comparePair[1]} onClose={() => setComparing(false)} />
      )}
    </Panel>
  );
}
