import { describe, expect, it } from "vitest";

import type { ChatEvent, MessageOut, ProgressEvent, Version } from "../api";
import { liveTurnFromEvents, messagesFromBlocks, toMessages } from "./chatModel";

function version(over: Partial<Version> = {}): Version {
  return {
    id: 1,
    project_id: 1,
    prompt: "a cube",
    code: "result=...",
    ok: true,
    error: null,
    volume: 8,
    bbox: [2, 2, 2],
    created_at: "",
    parameters: {},
    parent_version_id: null,
    plan_step: null,
    artifacts: [],
    ...over,
  };
}

describe("toMessages", () => {
  it("expands each version into a user + assistant turn", () => {
    const msgs = toMessages([version({ id: 1 }), version({ id: 2, prompt: "taller" })], [], false);
    expect(msgs.map((m) => m.kind)).toEqual(["user", "assistant", "user", "assistant"]);
    expect(msgs[0]).toMatchObject({ kind: "user", text: "a cube" });
    expect(msgs[3]).toMatchObject({ kind: "assistant", id: "a2" });
  });

  it("appends a live turn while generating, using the start event's intent", () => {
    const events: ProgressEvent[] = [
      { event: "start", intent: "a bracket", max_tries: 3 },
      { event: "stage", phase: "build", status: "begin", attempt: 1 },
    ];
    const msgs = toMessages([], events, true);
    expect(msgs.map((m) => m.kind)).toEqual(["user", "live"]);
    expect(msgs[0]).toMatchObject({ kind: "user", text: "a bracket" });
  });

  it("falls back to pendingText before the start event arrives", () => {
    const msgs = toMessages([], [], true, "optimistic prompt");
    expect(msgs[0]).toMatchObject({ kind: "user", text: "optimistic prompt" });
    expect(msgs[1].kind).toBe("live");
  });

  it("suppresses the live turn once its version has landed", () => {
    const events: ProgressEvent[] = [
      { event: "start", intent: "a cube", max_tries: 3 },
      { event: "done", version_id: 1, ok: true, attempt_count: 1 },
    ];
    const msgs = toMessages([version({ id: 1 })], events, false);
    // only the settled version's turn — no duplicate live turn
    expect(msgs.map((m) => m.kind)).toEqual(["user", "assistant"]);
  });

  it("keeps an error turn (no version created) visible", () => {
    const events: ProgressEvent[] = [
      { event: "start", intent: "a cube", max_tries: 3 },
      { event: "error", detail: "boom" },
    ];
    const msgs = toMessages([], events, false);
    expect(msgs.at(-1)?.kind).toBe("live");
  });
});

function msg(over: Partial<MessageOut> = {}): MessageOut {
  return {
    id: 1,
    seq: 1,
    role: "user",
    content: null,
    status: "ok",
    error: null,
    version_id: null,
    created_at: "",
    blocks: [],
    ...over,
  };
}

describe("messagesFromBlocks", () => {
  it("maps a user text block to a user message", () => {
    const msgs = messagesFromBlocks([
      msg({ id: 1, role: "user", blocks: [{ kind: "text", text: "make a **cube**" }] }),
    ]);
    expect(msgs).toEqual([{ kind: "text", id: "m1", role: "user", text: "make a **cube**" }]);
  });

  it("maps an assistant text block to an assistant markdown message", () => {
    const msgs = messagesFromBlocks([
      msg({ id: 2, role: "assistant", blocks: [{ kind: "text", text: "Here you go." }] }),
    ]);
    expect(msgs).toEqual([
      { kind: "text", id: "m2", role: "assistant", text: "Here you go." },
    ]);
  });

  it("maps an assistant message carrying a version_id to a result card", () => {
    const msgs = messagesFromBlocks([
      msg({ id: 3, role: "assistant", version_id: 9, blocks: [] }),
    ]);
    expect(msgs).toEqual([{ kind: "result", id: "m3", versionId: 9, ok: true, error: null }]);
  });

  it("flags a failed assistant turn on the result card", () => {
    const msgs = messagesFromBlocks([
      msg({ id: 4, role: "assistant", status: "error", error: "boom", version_id: 5, blocks: [] }),
    ]);
    expect(msgs[0]).toMatchObject({ kind: "result", versionId: 5, ok: false, error: "boom" });
  });

  it("maps a clarification block to a clarification message with questions + chips", () => {
    const msgs = messagesFromBlocks([
      msg({
        id: 6,
        role: "assistant",
        blocks: [
          {
            kind: "clarification",
            input: {
              questions: [
                { text: "Metric or imperial?", options: ["mm", "in"] },
                { text: "Through-hole or blind?" },
              ],
            },
          },
        ],
      }),
    ]);
    expect(msgs).toEqual([
      {
        kind: "clarification",
        id: "m6",
        questions: [
          { text: "Metric or imperial?", options: ["mm", "in"] },
          { text: "Through-hole or blind?", options: undefined },
        ],
      },
    ]);
  });

  it("maps a plan block to an ordered plan message ahead of the result card", () => {
    const msgs = messagesFromBlocks([
      msg({
        id: 9,
        role: "assistant",
        version_id: 42,
        blocks: [
          { kind: "plan", input: { steps: ["base plate", "bolt circle", "fillets"] } },
        ],
      }),
    ]);
    expect(msgs).toEqual([
      { kind: "plan", id: "m9-p", steps: ["base plate", "bolt circle", "fillets"] },
      { kind: "result", id: "m9", versionId: 42, ok: true, error: null },
    ]);
  });

  it("skips empty blocks and tool_use/tool_result blocks", () => {
    const msgs = messagesFromBlocks([
      msg({
        id: 5,
        role: "assistant",
        version_id: null,
        blocks: [
          { kind: "tool_use", id: "t1", name: "generate", input: {} },
          { kind: "tool_result", tool_use_id: "t1" },
        ],
      }),
    ]);
    expect(msgs).toEqual([]);
  });

  it("maps a thinking block to a collapsed thinking message before the text", () => {
    const msgs = messagesFromBlocks([
      msg({
        id: 8,
        role: "assistant",
        blocks: [
          { kind: "thinking", text: "let me reason" },
          { kind: "text", text: "Here you go." },
        ],
      }),
    ]);
    expect(msgs).toEqual([
      { kind: "thinking", id: "m8-t", text: "let me reason" },
      { kind: "text", id: "m8", role: "assistant", text: "Here you go." },
    ]);
  });
});

describe("liveTurnFromEvents", () => {
  it("accumulates text deltas into a single markdown string", () => {
    const events: ChatEvent[] = [
      { event: "turn_start" },
      { event: "text_delta", text: "Hello " },
      { event: "text_delta", text: "world" },
    ];
    const turn = liveTurnFromEvents(events);
    expect(turn.text).toBe("Hello world");
  });

  it("accumulates codegen deltas into a single code string", () => {
    const events: ChatEvent[] = [
      { event: "tool_start", tool: "generate_model", label: "Modeling" },
      { event: "codegen_delta", text: "from build123d import *\n" },
      { event: "codegen_delta", text: "result = Box(1, 1, 1)\n" },
    ];
    const turn = liveTurnFromEvents(events);
    expect(turn.codegen).toBe("from build123d import *\nresult = Box(1, 1, 1)\n");
  });

  it("maps nested tool_progress stages into staged-progress events", () => {
    const events: ChatEvent[] = [
      { event: "tool_start", tool: "generate", label: "Modeling" },
      { event: "tool_progress", stage: { event: "stage", phase: "build", status: "begin", attempt: 1 } },
      { event: "tool_progress", stage: { event: "stage", phase: "build", status: "ok", attempt: 1 } },
    ];
    const turn = liveTurnFromEvents(events);
    expect(turn.stageEvents).toEqual([
      { event: "stage", phase: "build", status: "begin", attempt: 1 },
      { event: "stage", phase: "build", status: "ok", attempt: 1 },
    ]);
  });

  it("exposes the settled result and stop reason", () => {
    const events: ChatEvent[] = [
      { event: "tool_result", version_id: 12, ok: true, metrics: null, thumbnail: null, tool: "generate", error: null },
      { event: "turn_end", stop_reason: "end_turn" },
    ];
    const turn = liveTurnFromEvents(events);
    expect(turn.result).toMatchObject({ versionId: 12, ok: true });
    expect(turn.stopReason).toBe("end_turn");
    expect(turn.done).toBe(true);
  });

  it("captures a clarification event's questions and ends the turn", () => {
    const events: ChatEvent[] = [
      {
        event: "clarification",
        questions: [{ text: "Round or square?", options: ["round", "square"] }],
      },
      { event: "turn_end", stop_reason: "clarification" },
    ];
    const turn = liveTurnFromEvents(events);
    expect(turn.clarification).toEqual([
      { text: "Round or square?", options: ["round", "square"] },
    ]);
    expect(turn.stopReason).toBe("clarification");
    expect(turn.done).toBe(true);
  });

  it("captures a plan event's ordered steps", () => {
    const events: ChatEvent[] = [
      { event: "plan", steps: ["sketch", "extrude", "fillet"] },
      { event: "tool_start", tool: "generate_model", label: "Generating model" },
    ];
    const turn = liveTurnFromEvents(events);
    expect(turn.plan).toEqual(["sketch", "extrude", "fillet"]);
  });

  it("reports an error event as a stopped/error state", () => {
    const events: ChatEvent[] = [{ event: "error", detail: "aborted" }];
    const turn = liveTurnFromEvents(events);
    expect(turn.error).toBe("aborted");
    expect(turn.done).toBe(true);
  });

  it("captures queued steer messages so they render in the transcript", () => {
    const events: ChatEvent[] = [
      { event: "tool_result", version_id: 1, ok: true, metrics: null, thumbnail: null, tool: "generate_model", error: null },
      { event: "steer", text: "make it red" },
      { event: "text_delta", text: "done, now red" },
      { event: "turn_end", stop_reason: "end_turn" },
    ];
    const turn = liveTurnFromEvents(events);
    expect(turn.steers).toEqual(["make it red"]);
  });

  it("accumulates thinking deltas into the live turn's thinking string", () => {
    const events: ChatEvent[] = [
      { event: "turn_start" },
      { event: "thinking_delta", text: "let me " },
      { event: "thinking_delta", text: "think" },
      { event: "text_delta", text: "answer" },
    ];
    const turn = liveTurnFromEvents(events);
    expect(turn.thinking).toBe("let me think");
    expect(turn.text).toBe("answer");
    expect(turn.done).toBe(false);
  });
});
