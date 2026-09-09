/** Derive a chat transcript from store state — a pure view over versions + the
 * live SSE event stream (no separate conversation persistence). Each version is
 * one turn: the user's prompt followed by the assistant's result. A generation
 * in flight appends a live turn, which is dropped once its version lands (the
 * `done` event's version_id appears in `versions`). Unit-tested. */
import type {
  ChatEvent,
  ClarificationQuestion,
  CritiqueView,
  MessageOut,
  ProgressEvent,
  Version,
} from "../api";

export type ChatMessage =
  | { kind: "user"; id: string; text: string }
  | { kind: "assistant"; id: string; version: Version }
  | { kind: "live"; id: "live"; events: ProgressEvent[] }
  // Block-based transcript: a markdown `text` turn, or an assistant
  // result that links a produced version (rendered with the result/action card).
  | { kind: "text"; id: string; role: string; text: string }
  // The model's reasoning, rendered as a collapsible "Thought" pane.
  | { kind: "thinking"; id: string; text: string }
  | { kind: "result"; id: string; versionId: number | null; ok: boolean; error: string | null }
  // A clarification turn: questions + optional quick-reply chips that
  // send a normal user message on click.
  | { kind: "clarification"; id: string; questions: ClarificationQuestion[] }
  // An ordered plan, rendered as a numbered list ahead of the action card.
  | { kind: "plan"; id: string; steps: string[] }
  // A picture belonging to one turn — a reference the user attached, or a render
  // the critique reviewer was shown. It carries where to ask for the bytes rather
  // than the bytes: the transcript hands back an image block with its `data`
  // emptied, and `index` is which of this message's images to fetch. `role` is
  // what tells the two apart, since by then both are just image blocks.
  | {
      kind: "image";
      id: string;
      role: string;
      messageId: number;
      index: number;
      mediaType: string | null;
      reading: string | null;
    }
  // The in-flight `POST /chat` turn, rendered incrementally from its SSE events.
  | { kind: "live-chat"; id: "live-chat"; turn: LiveTurn };

/** One round of the render critique: the renders the reviewer was shown, and the
 * verdict it wrote about them. `feedback` is empty when `matches` is true. */
export interface CritiqueRound {
  attempt: number;
  matches: boolean;
  feedback: string;
  views: CritiqueView[];
}

/** A live turn assembled from the `POST /chat` SSE event stream. */
export interface LiveTurn {
  /** Accumulated `text_delta`s as a single markdown string. */
  text: string;
  /** Accumulated `thinking_delta`s — the model's streamed reasoning. */
  thinking: string;
  /** Accumulated `codegen_delta`s — the build123d code streamed live as the
   * codegen model writes it during a fresh generate_model. */
  codegen: string;
  /** Stages lifted out of `tool_progress` events, fed to `StagedProgress`. */
  stageEvents: ProgressEvent[];
  /** The newest render-critique round, or null if the turn had none. */
  critique: CritiqueRound | null;
  /** The settled tool result, if one arrived. */
  result: { versionId: number | null; ok: boolean; error: string | null } | null;
  /** Clarification questions, if the turn ended asking for input. */
  clarification: ClarificationQuestion[] | null;
  /** The ordered plan steps, if the turn emitted a plan before acting. */
  plan: string[] | null;
  /** Queued/steer messages injected mid-run, in arrival order. */
  steers: string[];
  /** The turn's stop reason once it ends. */
  stopReason: string | null;
  /** An error detail when the turn faulted / was aborted. */
  error: string | null;
  /** True once the turn has ended (turn_end or error). */
  done: boolean;
}

/** Coerce a persisted clarification block's `input.questions` payload into typed
 * questions. Tolerant of the loose `Record<string, unknown>` shape. */
function questionsFromInput(input: Record<string, unknown> | null | undefined): ClarificationQuestion[] {
  const raw = input?.questions;
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((q): q is Record<string, unknown> => typeof q === "object" && q !== null)
    .map((q) => ({
      text: String(q.text ?? ""),
      options: Array.isArray(q.options) ? q.options.map(String) : undefined,
    }))
    .filter((q) => q.text);
}

/** Coerce a persisted plan block's `input.steps` payload into a string list
 *. Tolerant of the loose `Record<string, unknown>` shape. */
function stepsFromInput(input: Record<string, unknown> | null | undefined): string[] {
  const raw = input?.steps;
  if (!Array.isArray(raw)) return [];
  return raw.map(String).filter((s) => s.trim());
}

/** Map a block-based transcript (`GET /projects/{id}/messages`) to chat messages.
 * `text` blocks become markdown turns; an `image` block becomes a picture to
 * fetch; an assistant message that produced a version becomes a result card.
 * `tool_use`/`tool_result` blocks are internal and not surfaced. */
export function messagesFromBlocks(messages: MessageOut[]): ChatMessage[] {
  const out: ChatMessage[] = [];
  for (const m of messages) {
    const id = `m${m.id}`;
    const images: ChatMessage[] = m.blocks
      .filter((b) => b.kind === "image")
      .map((b, index) => ({
        kind: "image",
        id: `${id}-i${index}`,
        role: m.role,
        messageId: m.id,
        index,
        mediaType: b.media_type ?? null,
        reading: b.reading ?? null,
      }));
    // This function orders a turn by kind rather than by block position, and the
    // two roles were written in opposite orders. A user's attachments lead their
    // words, because the turn was sent as pictures plus a caption. The assistant's
    // are renders of what the turn built, taken while it was still working, so
    // they follow its account of it and sit against the result card below.
    if (m.role === "user") out.push(...images);
    // Reasoning, if present, leads the turn (collapsible "Thought" pane).
    const thinking = m.blocks
      .filter((b) => b.kind === "thinking" && b.text)
      .map((b) => b.text)
      .join("\n\n")
      .trim();
    if (thinking) out.push({ kind: "thinking", id: `${id}-t`, text: thinking });
    const text = m.blocks
      .filter((b) => b.kind === "text" && b.text)
      .map((b) => b.text)
      .join("\n\n")
      .trim();
    if (text) out.push({ kind: "text", id, role: m.role, text });
    // An ordered plan leads the action card it precedes.
    const planBlock = m.blocks.find((b) => b.kind === "plan");
    if (planBlock) {
      const steps = stepsFromInput(planBlock.input);
      if (steps.length) out.push({ kind: "plan", id: `${id}-p`, steps });
    }
    if (m.role !== "user") out.push(...images);
    const clarBlock = m.blocks.find((b) => b.kind === "clarification");
    if (clarBlock) {
      const questions = questionsFromInput(clarBlock.input);
      if (questions.length)
        out.push({ kind: "clarification", id: text ? `${id}-c` : id, questions });
    }
    if (m.role === "assistant" && m.version_id != null) {
      out.push({
        // Keep React keys unique when a turn carries both a text block and a card.
        kind: "result",
        id: text ? `${id}-r` : id,
        versionId: m.version_id,
        ok: m.status !== "error",
        error: m.error,
      });
    }
  }
  return out;
}

/** Build the full chat transcript: the persisted block-based messages plus the
 * in-flight `POST /chat` turn (optimistic user bubble + live assistant turn).
 * The live turn is shown while a chat turn is generating; once it settles the
 * reloaded `messages` carry it instead. */
export function chatTranscript(
  messages: MessageOut[],
  chatEvents: ChatEvent[],
  generating: boolean,
  pending: string | null,
): ChatMessage[] {
  const msgs = messagesFromBlocks(messages);
  if (generating || chatEvents.length > 0) {
    if (pending) msgs.push({ kind: "text", id: "live-user", role: "user", text: pending });
    const turn = liveTurnFromEvents(chatEvents);
    // Queued steer messages render as user bubbles in the transcript,
    // ahead of the live assistant turn that applies them at its next boundary.
    turn.steers.forEach((text, i) =>
      msgs.push({ kind: "text", id: `live-steer-${i}`, role: "user", text }),
    );
    msgs.push({ kind: "live-chat", id: "live-chat", turn });
  }
  return msgs;
}

/** Fold a `POST /chat` SSE event list into a `LiveTurn` view-model. */
export function liveTurnFromEvents(events: ChatEvent[]): LiveTurn {
  let text = "";
  let thinking = "";
  let codegen = "";
  const stageEvents: ProgressEvent[] = [];
  let critique: CritiqueRound | null = null;
  let result: LiveTurn["result"] = null;
  let clarification: ClarificationQuestion[] | null = null;
  let plan: string[] | null = null;
  const steers: string[] = [];
  let stopReason: string | null = null;
  let error: string | null = null;
  let done = false;

  for (const e of events) {
    switch (e.event) {
      case "text_delta":
        text += e.text;
        break;
      case "thinking_delta":
        thinking += e.text;
        break;
      case "codegen_delta":
        codegen += e.text;
        break;
      case "tool_progress":
        stageEvents.push(e.stage);
        break;
      case "critique":
        // Each round replaces the last rather than stacking. Only the round a
        // turn ended on is persisted, so a live turn that kept them all would
        // shed everything but the final one the moment the transcript reloaded.
        critique = { attempt: e.attempt, matches: e.matches, feedback: e.feedback, views: e.views };
        break;
      case "tool_result":
        result = { versionId: e.version_id, ok: e.ok, error: e.error };
        break;
      case "clarification":
        clarification = e.questions;
        break;
      case "plan":
        plan = e.steps;
        break;
      case "steer":
        steers.push(e.text);
        break;
      case "turn_end":
        stopReason = e.stop_reason;
        done = true;
        break;
      case "error":
        error = e.detail;
        done = true;
        break;
    }
  }
  return { text, thinking, codegen, plan, steers, stageEvents, critique, result, clarification, stopReason, error, done };
}

export function toMessages(
  versions: Version[],
  events: ProgressEvent[],
  generating: boolean,
  pendingText = "",
): ChatMessage[] {
  const msgs: ChatMessage[] = [];
  for (const v of versions) {
    msgs.push({ kind: "user", id: `u${v.id}`, text: v.prompt });
    msgs.push({ kind: "assistant", id: `a${v.id}`, version: v });
  }

  const start = events.find((e) => e.event === "start");
  const done = events.find((e) => e.event === "done");
  const settledId = done && done.event === "done" ? done.version_id : null;
  const settled = settledId != null && versions.some((v) => v.id === settledId);
  const showLive = (generating || events.length > 0) && !settled;

  if (showLive) {
    const text = (start && start.event === "start" ? start.intent : "") || pendingText;
    if (text) msgs.push({ kind: "user", id: "live-user", text });
    msgs.push({ kind: "live", id: "live", events });
  }

  return msgs;
}
