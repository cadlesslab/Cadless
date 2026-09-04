/** The chat input composer (controlled). Modern chat ergonomics: Enter sends,
 * Shift+Enter inserts a newline; the send button sits inside the field. One
 * message; the panel auto-picks generate vs refine under the hood.
 *
 * Mid-stream the field stays editable: the user can type and QUEUE a
 * steer message that the in-flight turn applies at its next boundary. Queuing is
 * a distinct affordance from Stop — Stop aborts the turn, Queue steers it. While
 * generating, Enter (and the Queue button) call `onQueue`; otherwise `onSubmit`.
 *
 * A turn can also carry reference pictures, pasted or picked from disk. They are
 * held here as chips until the turn goes out, and the limits are checked on this
 * side as well as the server's — refusing a 10MB screenshot after it has been
 * uploaded is the one refusal that costs the user something to receive. */
import { useRef, useState, type ClipboardEvent, type RefObject } from "react";

import { IMAGE_LIMITS, type ImageAttachment } from "../api";
import { Button, Textarea, Tooltip } from "../components";

/** An attachment while it is still in the composer: the wire fields plus what the
 * chips and the limit checks need. Neither extra field goes up with the turn.
 *
 * `bytes` is the file's own size and is kept rather than derived, because the
 * limit is on the decoded bytes and `data.length` is the base64 of them — a
 * third larger, which would refuse a turn the server would have taken. */
export interface ComposerAttachment extends ImageAttachment {
  name: string;
  bytes: number;
}

/** Read a picked file as the base64 the turn sends.
 *
 * `readAsDataURL` rather than `arrayBuffer` + `btoa`: the latter has to spread a
 * multi-megabyte byte array through `String.fromCharCode` to get there, which
 * overflows the call stack at exactly the sizes this feature is for. */
function readAsBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error ?? new Error("could not read the file"));
    reader.onload = () => {
      const url = String(reader.result ?? "");
      // A data URL is `data:<media-type>;base64,<payload>`, and the server wants
      // the payload on its own — it decodes what it is given rather than parsing
      // a URL out of it.
      resolve(url.slice(url.indexOf(",") + 1));
    };
    reader.readAsDataURL(file);
  });
}

/** A byte limit as the megabytes it was written as, so a refusal quotes the same
 * number the limit is set to rather than a binary-megabyte translation of it. */
function mb(bytes: number): string {
  return `${(bytes / 1_000_000).toFixed(2).replace(/\.?0+$/, "")} MB`;
}

/** Why these files cannot all be attached, or `null` when they can.
 *
 * Decided before anything is read: every limit is a property of the files as
 * picked, and `File.size` is already the decoded byte count the server measures
 * against, so nothing has to be encoded to find out that it will be refused.
 * Each message names the limit it hit — "too large" with no number leaves
 * someone resizing a screenshot by guesswork. */
function refusal(current: ComposerAttachment[], incoming: File[]): string | null {
  if (current.length + incoming.length > IMAGE_LIMITS.maxCount) {
    return `You can attach at most ${IMAGE_LIMITS.maxCount} images to one message.`;
  }
  let total = current.reduce((n, a) => n + a.bytes, 0);
  for (const file of incoming) {
    if (!IMAGE_LIMITS.mediaTypes.includes(file.type)) {
      const kinds = IMAGE_LIMITS.mediaTypes.map((t) => t.replace("image/", "")).join(", ");
      return `${file.name || "That file"} is not an image this can send — use ${kinds}.`;
    }
    if (file.size > IMAGE_LIMITS.maxBytes) {
      const limit = mb(IMAGE_LIMITS.maxBytes);
      return `${file.name || "That image"} is too large — one image can be at most ${limit}.`;
    }
    total += file.size;
  }
  if (total > IMAGE_LIMITS.maxTurnBytes) {
    const limit = mb(IMAGE_LIMITS.maxTurnBytes);
    return `The attachments come to more than ${limit}, which is the limit for one message.`;
  }
  return null;
}

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
  attachments = [],
  onAttachmentsChange,
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
  // Reference pictures for the next turn. Owned by the panel, like `value`, so
  // that clearing them is part of the same "the turn went out" step.
  attachments?: ComposerAttachment[];
  onAttachmentsChange?: (next: ComposerAttachment[]) => void;
}) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [attachError, setAttachError] = useState<string | null>(null);
  // What was last handed up, which is not the same thing as the prop. Reading a
  // file is async, so a second paste can start before the first one's result has
  // come back down — and merging onto the captured prop would make the second
  // write drop the first. A silently lost attachment is the one outcome this
  // feature exists to refuse, so the merge reads this instead.
  const handedUp = useRef<ComposerAttachment[]>(attachments);
  handedUp.current = attachments;
  const canAttach = Boolean(onAttachmentsChange) && !disabled && !generating;

  const hasText = value.trim().length > 0;
  // A picture on its own is a request — "make this" over a photograph of a part
  // is how someone actually asks — so the send button answers to either.
  const canSend = !generating && !disabled && (hasText || attachments.length > 0);
  // Steering takes words: there is nowhere for a picture to go in a message
  // injected into a running loop.
  const canQueue = generating && !disabled && hasText;

  async function attach(files: File[]) {
    if (!onAttachmentsChange || files.length === 0) return;
    // Refused once here so an obviously-too-big file is not read at all, and once
    // again after the read against whatever else landed meanwhile.
    const why = refusal(handedUp.current, files);
    if (why) {
      setAttachError(why);
      return;
    }
    setAttachError(null);
    try {
      const read = await Promise.all(
        files.map(async (file) => ({
          media_type: file.type,
          data: await readAsBase64(file),
          name: file.name,
          bytes: file.size,
        })),
      );
      const late = refusal(handedUp.current, files);
      if (late) {
        setAttachError(late);
        return;
      }
      const next = [...handedUp.current, ...read];
      handedUp.current = next;
      onAttachmentsChange(next);
    } catch {
      // Said rather than swallowed: a file that will not read leaves no chip, and
      // silence there looks exactly like an attachment that worked.
      setAttachError("That file could not be read.");
    }
  }

  function onPaste(e: ClipboardEvent<HTMLTextAreaElement>) {
    if (!canAttach) return;
    const list = e.clipboardData?.files;
    const files = list ? Array.from(list).filter((f) => f.type.startsWith("image/")) : [];
    if (files.length === 0) return;
    // Only once there is a picture to take: a paste with no image in it is
    // ordinary text, and preventing that would break pasting into the field.
    e.preventDefault();
    void attach(files);
  }

  function remove(index: number) {
    setAttachError(null);
    const next = handedUp.current.filter((_, i) => i !== index);
    handedUp.current = next;
    onAttachmentsChange?.(next);
  }

  return (
    <div className="composer">
      {(onToggleForge || onAttachmentsChange) && (
        <div className="composer-tools">
          {onToggleForge && (
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
          )}
          {onAttachmentsChange && (
            <Tooltip label="Attach a reference image — a photo or a sketch of the part you want.">
              <Button
                className="composer-attach"
                variant="ghost"
                aria-label="Attach image"
                disabled={!canAttach}
                onClick={() => fileRef.current?.click()}
              >
                ⊕ Image
              </Button>
            </Tooltip>
          )}
        </div>
      )}
      {/* Hidden rather than styled: a file input cannot be restyled to match the
          rest of the row in any browser, so the visible control is the button
          above and this is only the picker it opens. */}
      {onAttachmentsChange && (
        <input
          ref={fileRef}
          type="file"
          accept="image/*"
          multiple
          className="composer-file"
          aria-label="Attach image files"
          onChange={(e) => {
            const picked = e.target.files;
            void attach(picked ? Array.from(picked) : []);
            // Cleared so picking the same file twice in a row fires `change`
            // again — otherwise the second attempt is silently nothing.
            e.target.value = "";
          }}
        />
      )}
      {attachments.length > 0 && (
        <ul className="composer-chips" aria-label="Attached images">
          {attachments.map((a, i) => (
            <li className="composer-chip" key={`${a.name}-${i}`}>
              <span className="composer-chip-name" title={a.name}>
                {a.name}
              </span>
              <button
                type="button"
                className="composer-chip-remove"
                aria-label={`Remove ${a.name}`}
                onClick={() => remove(i)}
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      )}
      {attachError && (
        <p className="composer-attach-error" role="alert">
          {attachError}
        </p>
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
          onPaste={onPaste}
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
