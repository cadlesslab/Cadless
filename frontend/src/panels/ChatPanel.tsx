/** Right-side conversational panel: a scrolling transcript derived from the
 * version history + live SSE events, with the composer pinned at the bottom.
 * Owns the generate/refine/retry/recall logic (ported from the old PromptBar);
 * ChatComposer and ChatMessage are presentational. */
import { useEffect, useRef, useState } from "react";

import { Button, IconButton } from "../components";
import { useActiveProject, useStoreSelector } from "../state";
import { useApp } from "../useApp";
import { ChatComposer } from "./ChatComposer";
import { ChatMessage } from "./ChatMessage";
import { chatTranscript } from "./chatModel";
import { EXAMPLE_PROMPTS } from "./examples";

function EmptyState({ onPick, disabled }: { onPick: (p: string) => void; disabled: boolean }) {
  return (
    <div className="chat-empty">
      <p className="chat-empty-title">Describe a part and I’ll model it.</p>
      <div className="examples" aria-label="Example prompts">
        {EXAMPLE_PROMPTS.map((ex) => (
          <button
            key={ex.title}
            className="example-chip"
            title={ex.prompt}
            disabled={disabled}
            onClick={() => onPick(ex.prompt)}
          >
            {ex.title}
          </button>
        ))}
      </div>
    </div>
  );
}

/** @param visible — whether this panel is actually on screen. It is not always:
 *  in the narrow layout the shell keeps the panel mounted and hides it, so that
 *  a half-typed prompt survives the drawer closing. Anything that measures or
 *  focuses has to wait for the panel to come back, because both are no-ops
 *  inside a `display: none` subtree and neither reports that it did nothing.
 *  @param collapseLabel — what the close control is called. The wide layout
 *  collapses to a strip and the narrow one closes a drawer, and a control that
 *  named the wrong one would be the only wrong word on the screen. */
export function ChatPanel({
  onCollapse,
  onReveal,
  visible = true,
  collapseLabel = "Collapse chat",
}: {
  onCollapse?: () => void;
  onReveal?: () => void;
  visible?: boolean;
  collapseLabel?: string;
}) {
  const app = useApp();
  const messagesData = useStoreSelector((s) => s.messages);
  const chatEvents = useStoreSelector((s) => s.chatEvents);
  const chatPending = useStoreSelector((s) => s.chatPending);
  const generating = useStoreSelector((s) => s.generating);
  const activeProjectId = useStoreSelector((s) => s.activeProjectId);
  const activeProject = useActiveProject();
  const recalled = useStoreSelector((s) => s.recalledPrompt);

  const [value, setValue] = useState("");
  // Per-turn forge opt-in: when on, the next turn races best-of-N for a
  // fresh generation (server gates it behind the global forge kill-switch too).
  const [forge, setForge] = useState(false);
  const lastText = useRef("");
  const replay = useRef<() => void>(() => {});
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const threadRef = useRef<HTMLDivElement>(null);

  // Transcript = persisted block-based messages + the live POST /chat turn.
  const messages = chatTranscript(messagesData, chatEvents, generating, chatPending);

  // A version's prompt was recalled into the composer. Hidden, the focus call
  // below does nothing, so the shell is told to bring the panel back first and
  // the recall waits here for it — otherwise recalling from the rail while the
  // drawer is closed looks like nothing happened at all.
  useEffect(() => {
    if (recalled == null) return;
    if (!visible) {
      onReveal?.();
      return;
    }
    setValue(recalled);
    inputRef.current?.focus();
    app.clearRecalled();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [recalled, visible]);

  // Stick to the newest message as the thread grows / streams. `visible` is a
  // dependency because a hidden thread has no layout: messages that arrive
  // while the drawer is closed would otherwise leave it scrolled to the oldest
  // one, which is where it opens.
  useEffect(() => {
    if (!visible) return;
    const el = threadRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages.length, chatEvents.length, generating, visible]);

  // Catalog items are read-only, and the backend refuses a chat turn on one with
  // a 403. Follow the convention the other panels use (VersionsPanel rerun,
  // ParametersPanel edit) and take the control away rather than surfacing the
  // error. Taking it away silently is its own dead end, though, so the note
  // rendered just above the composer says why the field is dead and carries the
  // way out — the same place the user hits the wall, not up in the header.
  const readOnly = activeProject?.is_catalog === true;

  function runChat(message: string) {
    if (generating || activeProjectId == null || readOnly) return;
    lastText.current = message;
    const opted = forge;
    replay.current = () => {
      if (generating || activeProjectId == null) return;
      app.chat(message, opted);
    };
    app.chat(message, opted);
  }
  function submit() {
    const text = value.trim();
    if (!text || generating || activeProjectId == null) return;
    runChat(text);
    setValue("");
  }
  // Queue a steer message mid-stream: distinct from submit/Stop. The
  // running turn injects it at its next agent-loop boundary.
  function queue() {
    const text = value.trim();
    if (!text || !generating || activeProjectId == null) return;
    void app.steerChat(text);
    setValue("");
  }
  function editLast() {
    setValue(lastText.current);
    inputRef.current?.focus();
  }

  const showEmpty = messages.length === 0 && !generating;

  return (
    <section className="chat" aria-label="Cadless">
      <header className="chat-head">
        <div className="chat-head-brand">
          <span className="chat-title">Cadless</span>
          {activeProject && (
            <>
              <span className="chat-title-sep" aria-hidden>
                /
              </span>
              <span className="chat-project" title={activeProject.name}>
                {activeProject.name}
                {activeProject.is_catalog && (
                  <span className="chat-project-badge">catalog</span>
                )}
              </span>
              {/* Provenance chip (#22): a clone links back to its baseline. */}
              {activeProject.derived_from_project_id != null && (
                <button
                  className="chat-derived-from"
                  aria-label={`Based on ${activeProject.derived_from_name ?? "original"}`}
                  title="Open the catalog item this project was customized from"
                  onClick={() =>
                    void app.selectProject(activeProject.derived_from_project_id!)
                  }
                >
                  based on {activeProject.derived_from_name ?? "original"}
                </button>
              )}
            </>
          )}
        </div>
        {onCollapse && (
          <IconButton label={collapseLabel} onClick={onCollapse}>
            ⟩
          </IconButton>
        )}
      </header>

      <div className="chat-thread" ref={threadRef}>
        {showEmpty ? (
          <EmptyState onPick={(p) => runChat(p)} disabled={activeProjectId == null || readOnly} />
        ) : (
          messages.map((m) => (
            <ChatMessage
              key={m.id}
              msg={m}
              app={app}
              onRetry={() => replay.current()}
              onEdit={editLast}
            />
          ))
        )}
      </div>

      {/* Customize-from-catalog (#22), moved down out of the header: the composer
          below is disabled, so the reason and the remedy belong next to it.
          Cloning switches into the editable copy, and this note goes with it. */}
      {readOnly && activeProject && (
        <div className="chat-readonly-note">
          <p>Catalog items are read-only. Customize this item to edit it with chat.</p>
          <Button
            title="Clone into an editable copy and start modifying it"
            onClick={() =>
              void app.cloneCatalogItem(activeProject.id, `${activeProject.name} (copy)`)
            }
          >
            Customize
          </Button>
        </div>
      )}

      <ChatComposer
        inputRef={inputRef}
        value={value}
        onChange={setValue}
        onSubmit={submit}
        onStop={app.stopChat}
        onQueue={queue}
        generating={generating}
        disabled={activeProjectId == null || readOnly}
        forge={forge}
        onToggleForge={() => setForge((f) => !f)}
      />
    </section>
  );
}
