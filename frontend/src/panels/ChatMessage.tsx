/** Renders one chat turn: a user bubble, an assistant result card, or the live
 * in-flight assistant turn (streaming staged progress). Reuses the version
 * thumbnail cache, CodePanel, and the version actions from the History panel. */
import { useEffect, useRef, useState } from "react";

import { attachmentUrl } from "../api";
import type { ClarificationQuestion, Version } from "../api";
import { Button, IconButton, CadlessIcon } from "../components";
import { useStoreSelector } from "../state";
import type { useApp } from "../useApp";
import type { CritiqueRound, LiveTurn, ChatMessage as Msg } from "./chatModel";
import { CodePanel } from "./CodePanel";
import { Markdown } from "./markdown";
import { StagedProgress } from "./StagedProgress";
import { useThumbnail } from "./thumbnails";

type App = ReturnType<typeof useApp>;

function Thumb({ id, ok }: { id: number; ok: boolean }) {
  const src = useThumbnail(id);
  return (
    <span className="ver-thumb">
      {src ? (
        <img src={src} alt="" />
      ) : (
        <span className={`ver-thumb-ph ${ok ? "" : "bad"}`}>{ok ? "◳" : "⚠"}</span>
      )}
    </span>
  );
}

function metricsLine(v: Version): string {
  const parts: string[] = [];
  if (v.volume != null) parts.push(`${v.volume.toFixed(1)} mm³`);
  if (v.bbox) parts.push(v.bbox.map((d) => d.toFixed(1)).join(" × ") + " mm");
  return parts.join(" · ");
}

function AssistantResult({ v, app }: { v: Version; app: App }) {
  const [showCode, setShowCode] = useState(false);
  // Catalog items are read-only: re-running would overwrite baked artifacts at
  // the wrong scale (#31), and the backend refuses it with a 403.
  const isCatalog = useStoreSelector(
    (s) => s.projects.find((p) => p.id === v.project_id)?.is_catalog ?? false,
  );
  return (
    <div className={`result-card ${v.ok ? "" : "bad"}`}>
      <button
        className="result-main"
        onClick={() => app.showVersion(v.id)}
        title={`Show v${v.id} in viewport`}
      >
        <Thumb id={v.id} ok={v.ok} />
        <span className="result-meta">
          <span className="result-top">
            <span className={v.ok ? "ok" : "bad"}>v{v.id}</span>
            {v.parent_version_id != null && (
              <span className="ver-lineage" title={`refined from v${v.parent_version_id}`}>
                ↳ v{v.parent_version_id}
              </span>
            )}
          </span>
          {v.ok ? (
            <span className="result-metrics">{metricsLine(v)}</span>
          ) : (
            <span className="result-error">{v.error ?? "Generation failed."}</span>
          )}
        </span>
      </button>

      <div className="result-actions">
        {v.code && (
          <Button size="sm" variant="ghost" onClick={() => setShowCode((s) => !s)}>
            {showCode ? "Hide code" : "View code"}
          </Button>
        )}
        <IconButton label={`Use v${v.id} prompt`} onClick={() => app.recallPrompt(v.prompt)}>
          ↩
        </IconButton>
        {!isCatalog && (
          <IconButton label={`Re-run v${v.id}`} onClick={() => void app.rerunVersion(v.id)}>
            ↻
          </IconButton>
        )}
      </div>

      {showCode && v.code && <CodePanel code={v.code} error={v.error} />}
    </div>
  );
}

/** Result card for a block-based assistant turn, resolving the version by id. */
function ResultByVersion({
  versionId,
  ok,
  error,
  app,
}: {
  versionId: number | null;
  ok: boolean;
  error: string | null;
  app: App;
}) {
  const version = useStoreSelector((s) => s.versions.find((v) => v.id === versionId) ?? null);
  if (version) return <AssistantResult v={version} app={app} />;
  // Version not loaded into the store (e.g. transcript ahead of versions).
  return (
    <div className={`result-card ${ok ? "" : "bad"}`}>
      <span className="result-meta">
        {ok ? (
          <span className="ok">v{versionId}</span>
        ) : (
          <span className="result-error">{error ?? "Generation failed."}</span>
        )}
      </span>
    </div>
  );
}

/** The model's reasoning as a collapsible "Thought for Ns" pane.
 *
 * While a turn is `streaming` the pane is open so the reasoning streams in live;
 * once the turn completes (or for a persisted, already-finished turn) it is
 * collapsed by default — the user can re-expand it to inspect the reasoning. The
 * elapsed time is measured while streaming and frozen on completion. */
function ThinkingPane({ text, streaming = false }: { text: string; streaming?: boolean }) {
  const [open, setOpen] = useState(streaming);
  const [seconds, setSeconds] = useState(0);
  const startRef = useRef<number | null>(null);

  // Collapse automatically once streaming ends (turn completed).
  const wasStreaming = useRef(streaming);
  useEffect(() => {
    if (wasStreaming.current && !streaming) setOpen(false);
    wasStreaming.current = streaming;
  }, [streaming]);

  // Tick the elapsed-seconds counter while the reasoning is streaming.
  useEffect(() => {
    if (!streaming) return;
    if (startRef.current === null) startRef.current = Date.now();
    const id = setInterval(() => {
      setSeconds(Math.floor((Date.now() - (startRef.current ?? Date.now())) / 1000));
    }, 250);
    return () => clearInterval(id);
  }, [streaming]);

  const label = streaming
    ? `Thinking${seconds > 0 ? ` for ${seconds}s` : "…"}`
    : seconds > 0
      ? `Thought for ${seconds}s`
      : "Thought";

  return (
    <div className={`thinking-pane ${open ? "open" : ""}`}>
      <button
        type="button"
        className="thinking-toggle"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        <span className="thinking-caret" aria-hidden>
          {open ? "▾" : "▸"}
        </span>
        {label}
      </button>
      {open && <div className="thinking-body">{text}</div>}
    </div>
  );
}

/** The codegen model's build123d code, streamed live as it is written, in a
 * collapsible pane that auto-collapses once the turn settles (/3533).
 * Mirrors {@link ThinkingPane}: while live it shows the intermediate output; when
 * done it folds away to a one-line summary, re-expandable. A plain `<pre>` re-
 * renders cheaply per delta (no markdown parse), so the only streaming concern is
 * keeping the latest line in view — handled by the auto-scroll effect. */
function CodegenPane({ code, streaming }: { code: string; streaming: boolean }) {
  const [open, setOpen] = useState(streaming);
  const wasStreaming = useRef(streaming);
  const bodyRef = useRef<HTMLPreElement>(null);

  useEffect(() => {
    if (wasStreaming.current && !streaming) setOpen(false); // collapse when done
    wasStreaming.current = streaming;
  }, [streaming]);

  useEffect(() => {
    if (open && streaming && bodyRef.current) {
      bodyRef.current.scrollTop = bodyRef.current.scrollHeight; // follow the stream
    }
  }, [code, open, streaming]);

  const lines = code ? code.split("\n").length : 0;
  const label = streaming ? "Writing code…" : `Wrote ${lines} line${lines === 1 ? "" : "s"}`;

  return (
    <div className={`codegen-pane ${open ? "open" : ""}`}>
      <button
        type="button"
        className="thinking-toggle"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        <span className="thinking-caret" aria-hidden>
          {open ? "▾" : "▸"}
        </span>
        {label}
      </button>
      {open && (
        <pre ref={bodyRef} className="codegen-code">
          <code>{code}</code>
        </pre>
      )}
    </div>
  );
}

/** A clarification turn: the assistant's questions, each optionally
 * with quick-reply chips. Clicking a chip sends its label as a normal user
 * message (`app.chat`), which answers the question and resumes the conversation —
 * the same as typing the answer. */
function Clarification({ questions, app }: { questions: ClarificationQuestion[]; app: App }) {
  return (
    <div className="clarification">
      {questions.map((q, i) => (
        <div className="clarify-q" key={i}>
          <p className="clarify-text">{q.text}</p>
          {q.options && q.options.length > 0 && (
            <div className="clarify-chips">
              {q.options.map((opt) => (
                <button
                  key={opt}
                  type="button"
                  className="clarify-chip"
                  onClick={() => app.chat(opt)}
                >
                  {opt}
                </button>
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

/** A picture belonging to a turn, as it comes back on a reload — a reference the
 * user attached, or a render the critique reviewer was shown.
 *
 * The `src` is a fetch rather than an inlined payload: the transcript hands back
 * an image block with its `data` emptied, so the only place the bytes exist is
 * behind the attachment route — and a session with four pictures in it would
 * otherwise re-download all of them inside every transcript response. */
function Attachment({
  messageId,
  index,
  mediaType,
  reading,
  fallbackAlt,
}: {
  messageId: number;
  index: number;
  mediaType: string | null;
  reading: string | null;
  /** What to say about the picture when no reading was written. The caller
   * supplies it because only the caller knows whose picture this is. */
  fallbackAlt: string;
}) {
  const projectId = useStoreSelector((s) => s.activeProjectId);
  if (projectId == null) return null;
  return (
    <img
      className="msg-image"
      src={attachmentUrl(projectId, messageId, index)}
      /* The reading is a model's written account of the picture, which makes it
         the only alt text anybody actually wrote. Without one the media type is
         all the transcript still knows, and saying which kind of file was handed
         over beats an empty alt on an image that carries the whole request. */
      alt={reading ?? `${fallbackAlt}${mediaType ? ` (${mediaType})` : ""}`}
      title={reading ?? undefined}
    />
  );
}

/** One round of the render critique: the views the reviewer was shown, and the
 * verdict it wrote about them.
 *
 * The bytes are inlined here rather than fetched the way {@link Attachment} does
 * it, because a round streams in while the turn that produced it is still
 * running — there is no persisted message yet to ask the attachment route for,
 * and showing the work as it happens is the whole reason this arrives live.
 *
 * The verdict is a sentence, and its colour repeats what the sentence already
 * says: a reader who cannot tell the two colours apart still gets the verdict. */
function RenderCritique({ round }: { round: CritiqueRound }) {
  const verdict = round.matches
    ? "The render matches the request."
    : round.feedback || "The reviewer asked for another attempt.";
  return (
    <figure className="critique">
      <div className="critique-views">
        {round.views.map((v) => (
          <img
            key={v.name}
            className="critique-view"
            src={`data:image/png;base64,${v.png_b64}`}
            alt={`The part as built, seen from the ${v.name}`}
          />
        ))}
      </div>
      <figcaption className="critique-verdict">
        {`Review of attempt ${round.attempt}: `}
        <span className={round.matches ? "ok" : "bad"}>{verdict}</span>
      </figcaption>
    </figure>
  );
}

/** An ordered plan: the steps the assistant intends to take for a
 * non-trivial part, rendered as a numbered list ahead of the action card. */
function PlanList({ steps }: { steps: string[] }) {
  return (
    <ol className="plan-list">
      {steps.map((step, i) => (
        <li key={i} className="plan-step">
          {step}
        </li>
      ))}
    </ol>
  );
}

/** The in-flight `POST /chat` turn: streamed markdown + nested StagedProgress +
 * the settled result card, plus a stopped/error note. */
function LiveChat({
  turn,
  app,
  onRetry,
  onEdit,
}: {
  turn: LiveTurn;
  app: App;
  onRetry: () => void;
  onEdit: () => void;
}) {
  // Surface a turn-level error (abort / provider failure) through the reused
  // StagedProgress error UI, so it settles to a stopped state with Retry/Edit.
  const stageEvents = turn.error
    ? [...turn.stageEvents, { event: "error", detail: turn.error } as const]
    : turn.stageEvents;
  const streaming = !turn.done;
  // Nothing surfaced yet but the turn is live → show a "thinking" placeholder so
  // the panel never looks frozen while the model reasons before its first token.
  // A critique round counts as content here even though the stages it belongs to
  // do not: stage events are collected and delivered in one burst once the tool
  // settles, so a turn deep in the repair loop has captures on screen and an
  // empty `stageEvents` — and would otherwise claim to be waiting on its first
  // token while showing the reviewer's renders.
  const idle =
    streaming &&
    !turn.text &&
    !turn.thinking &&
    !turn.codegen &&
    !turn.critique &&
    turn.stageEvents.length === 0 &&
    !(turn.plan && turn.plan.length > 0) &&
    !(turn.clarification && turn.clarification.length > 0);
  return (
    <>
      {turn.thinking && <ThinkingPane text={turn.thinking} streaming={!turn.done} />}
      {turn.codegen && <CodegenPane code={turn.codegen} streaming={!turn.done} />}
      {turn.text && (
        <div className="live-text">
          <Markdown text={turn.text} />
          {streaming && <span className="stream-cursor" aria-hidden />}
        </div>
      )}
      {idle && (
        <p className="chat-thinking" role="status">
          Thinking<span className="chat-thinking-dots" aria-hidden />
        </p>
      )}
      {turn.plan && turn.plan.length > 0 && <PlanList steps={turn.plan} />}
      {stageEvents.length > 0 && (
        <StagedProgress events={stageEvents} onRetry={onRetry} onEdit={onEdit} />
      )}
      {turn.critique && <RenderCritique round={turn.critique} />}
      {turn.result && turn.result.versionId != null && (
        <ResultByVersion
          versionId={turn.result.versionId}
          ok={turn.result.ok}
          error={turn.result.error}
          app={app}
        />
      )}
      {turn.clarification && turn.clarification.length > 0 && (
        <Clarification questions={turn.clarification} app={app} />
      )}
    </>
  );
}

export function ChatMessage({
  msg,
  app,
  onRetry,
  onEdit,
}: {
  msg: Msg;
  app: App;
  onRetry: () => void;
  onEdit: () => void;
}) {
  // A picture takes its side of the thread from its role, ahead of the
  // fallthrough below — which takes any unbranched kind to be an assistant result
  // and reads `version` off it. Which side is not decoration: an attachment shown
  // under the assistant avatar reads as something the model produced rather than
  // something it was handed, and a render the model made, shown in the user's
  // bubble, reads as a reference the user supplied.
  if (msg.kind === "image") {
    if (msg.role === "user") {
      return (
        <div className="msg msg-user">
          <div className="msg-bubble msg-bubble-image">
            <Attachment
              messageId={msg.messageId}
              index={msg.index}
              mediaType={msg.mediaType}
              reading={msg.reading}
              fallbackAlt="Attached image"
            />
          </div>
        </div>
      );
    }
    // No avatar: a settled round comes back as one of these per view, and a
    // repeated avatar beside each reads as several things the model said. The
    // thread gathers a run of them into one grid, so they arrive as a block.
    return (
      <div className="msg msg-assistant msg-capture">
        <div className="msg-body">
          <Attachment
            messageId={msg.messageId}
            index={msg.index}
            mediaType={msg.mediaType}
            reading={msg.reading}
            fallbackAlt="A render of the part this turn built"
          />
        </div>
      </div>
    );
  }

  if (msg.kind === "user" || (msg.kind === "text" && msg.role === "user")) {
    return (
      <div className="msg msg-user">
        <div className="msg-bubble">{msg.text}</div>
      </div>
    );
  }

  return (
    <div className="msg msg-assistant">
      <span className="msg-avatar" aria-hidden>
        <CadlessIcon size={16} />
      </span>
      <div className="msg-body">
        {msg.kind === "live" ? (
          <StagedProgress events={msg.events} onRetry={onRetry} onEdit={onEdit} />
        ) : msg.kind === "live-chat" ? (
          <LiveChat turn={msg.turn} app={app} onRetry={onRetry} onEdit={onEdit} />
        ) : msg.kind === "thinking" ? (
          <ThinkingPane text={msg.text} />
        ) : msg.kind === "text" ? (
          <Markdown text={msg.text} />
        ) : msg.kind === "clarification" ? (
          <Clarification questions={msg.questions} app={app} />
        ) : msg.kind === "plan" ? (
          <PlanList steps={msg.steps} />
        ) : msg.kind === "result" ? (
          <ResultByVersion
            versionId={msg.versionId}
            ok={msg.ok}
            error={msg.error}
            app={app}
          />
        ) : (
          <AssistantResult v={msg.version} app={app} />
        )}
      </div>
    </div>
  );
}
