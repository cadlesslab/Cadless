/** The chat input composer (controlled). Modern chat ergonomics: Enter sends,
 * Shift+Enter inserts a newline; the send button sits inside the field. One
 * message; the panel auto-picks generate vs refine under the hood.
 *
 * Mid-stream the field stays editable: the user can type and QUEUE a
 * steer message that the in-flight turn applies at its next boundary. Queuing is
 * a distinct affordance from Stop — Stop aborts the turn, Queue steers it. While
 * generating, Enter (and the Queue button) call `onQueue`; otherwise `onSubmit`. */
import type { RefObject } from "react";

import { Button, Textarea, Tooltip } from "../components";

export function ChatComposer({
  inputRef,
  value,
  onChange,
  onSubmit,
  onStop,
  onQueue,
  generating,
  disabled,
  forge = false,
  onToggleForge,
}: {
  inputRef: RefObject<HTMLTextAreaElement>;
  value: string;
  onChange: (v: string) => void;
  onSubmit: () => void;
  onStop?: () => void;
  onQueue?: () => void;
  generating: boolean;
  disabled: boolean;
  // Per-turn forge opt-in: best-of-N racing for the next fresh
  // generation. Off by default; the server gates it behind a global kill-switch.
  forge?: boolean;
  onToggleForge?: () => void;
}) {
  const hasText = value.trim().length > 0;
  const canSend = !generating && !disabled && hasText;
  const canQueue = generating && !disabled && hasText;

  return (
    <div className="composer">
      {onToggleForge && (
        <div className="composer-tools">
          <Tooltip label="Forge: race best-of-N candidates and keep the best (costs more). Off by default.">
            <Button
              className="composer-forge"
              variant="ghost"
              aria-label="Forge"
              aria-pressed={forge}
              data-active={forge}
              disabled={disabled || generating}
              onClick={onToggleForge}
            >
              ⚒ Forge
            </Button>
          </Tooltip>
        </div>
      )}
      <div className="composer-field">
        <Textarea
          ref={inputRef}
          id="prompt-input"
          className="composer-input"
          rows={1}
          placeholder={
            generating ? "Queue a message to steer…" : "Describe or refine your part…"
          }
          value={value}
          disabled={disabled}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              if (generating) onQueue?.();
              else onSubmit();
            }
          }}
        />
        {generating && (
          <Button
            className="composer-send composer-queue"
            variant="ghost"
            aria-label="Queue message"
            title="Queue a message to steer this turn"
            disabled={!canQueue || !onQueue}
            onClick={onQueue}
          >
            ⤵
          </Button>
        )}
        {generating ? (
          <Button
            className="composer-send composer-stop heartbeat"
            variant="ghost"
            aria-label="Stop"
            title="Working… click to stop"
            disabled={!onStop}
            onClick={onStop}
          >
            ■
          </Button>
        ) : (
          <Button
            className="composer-send"
            variant="primary"
            aria-label="Send"
            title="Send"
            disabled={!canSend}
            onClick={onSubmit}
          >
            ↑
          </Button>
        )}
      </div>
    </div>
  );
}
