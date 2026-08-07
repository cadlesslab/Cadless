/** Projects flyout: list/select projects and create/rename/delete them.
 * Replaces the old top-bar Projects menu; reuses the shared app actions. */
import { useState } from "react";

import type { Project } from "../api";
import {
  Button,
  ConfirmDialog,
  EmptyState,
  IconButton,
  Panel,
  PromptDialog,
  Tooltip,
} from "../components";
import { useStoreSelector } from "../state";
import { useApp } from "../useApp";

type Dialog =
  | { kind: "create" }
  | { kind: "rename"; project: Project }
  | { kind: "delete"; project: Project }
  | null;

export function ProjectsPanel() {
  const app = useApp();
  const projects = useStoreSelector((s) => s.projects);
  const activeProjectId = useStoreSelector((s) => s.activeProjectId);
  const [dialog, setDialog] = useState<Dialog>(null);

  return (
    <Panel
      title="Projects"
      actions={
        <Tooltip label="New project">
          <Button size="sm" variant="primary" onClick={() => setDialog({ kind: "create" })}>
            New
          </Button>
        </Tooltip>
      }
    >
      {projects.length === 0 ? (
        <EmptyState>No projects yet.</EmptyState>
      ) : (
        <ul className="proj-list">
          {projects.map((p) => (
            <li key={p.id} className={`proj-item ${p.id === activeProjectId ? "active" : ""}`}>
              <button
                className="proj-name"
                aria-current={p.id === activeProjectId}
                onClick={() => void app.selectProject(p.id)}
              >
                {p.name}
              </button>
              {/* A catalog item is read-only, so it gets neither control. Taking the
                  control away is how the rest of the app says so — ChatPanel,
                  ParametersPanel and VersionsPanel all gate on is_catalog rather
                  than letting the server's refusal arrive as an error toast. */}
              {!p.is_catalog && (
                <span className="proj-actions">
                  <IconButton label={`Rename ${p.name}`} onClick={() => setDialog({ kind: "rename", project: p })}>
                    ✎
                  </IconButton>
                  <IconButton label={`Delete ${p.name}`} onClick={() => setDialog({ kind: "delete", project: p })}>
                    🗑
                  </IconButton>
                </span>
              )}
            </li>
          ))}
        </ul>
      )}

      <PromptDialog
        open={dialog?.kind === "create"}
        title="New project"
        label="Project name"
        confirmLabel="Create"
        onSubmit={(name) => {
          setDialog(null);
          void app.createProject(name);
        }}
        onClose={() => setDialog(null)}
      />
      <PromptDialog
        open={dialog?.kind === "rename"}
        title="Rename project"
        label="Project name"
        initialValue={dialog?.kind === "rename" ? dialog.project.name : ""}
        onSubmit={(name) => {
          if (dialog?.kind === "rename") void app.renameProject(dialog.project.id, name);
          setDialog(null);
        }}
        onClose={() => setDialog(null)}
      />
      <ConfirmDialog
        open={dialog?.kind === "delete"}
        title="Delete project?"
        message={
          dialog?.kind === "delete"
            ? `"${dialog.project.name}" and all its versions will be removed. This cannot be undone.`
            : ""
        }
        onConfirm={() => {
          if (dialog?.kind === "delete") void app.removeProject(dialog.project.id);
          setDialog(null);
        }}
        onClose={() => setDialog(null)}
      />
    </Panel>
  );
}
