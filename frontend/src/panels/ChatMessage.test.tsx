import { fireEvent } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { Version } from "../api";
import { renderWithProviders } from "../test/utils";
import { ChatMessage } from "./ChatMessage";
import type { LiveTurn, ChatMessage as Msg } from "./chatModel";

function okVersion(): Version {
  return {
    id: 7, project_id: 1, prompt: "a cube", code: "result=...", ok: true, error: null,
    volume: 1, bbox: [1, 1, 1], created_at: "", parameters: {}, parent_version_id: null,
    plan_step: null, artifacts: [],
  };
}

// The component only invokes `app` inside event handlers we never trigger here.
const app = {} as Parameters<typeof ChatMessage>[0]["app"];

function renderMsg(msg: Msg) {
  return renderWithProviders(
    <ChatMessage msg={msg} app={app} onRetry={() => {}} onEdit={() => {}} />,
  );
}

function liveTurn(over: Partial<LiveTurn> = {}): LiveTurn {
  return {
    text: "", thinking: "", codegen: "", plan: null, steers: [], stageEvents: [],
    result: null, clarification: null, stopReason: null, error: null, done: false,
    ...over,
  };
}

describe("ChatMessage", () => {
  it("renders the CadlessIcon spark as the assistant avatar", () => {
    const { container } = renderMsg({ kind: "assistant", id: "a7", version: okVersion() });
    const avatar = container.querySelector(".msg-avatar");
    expect(avatar).not.toBeNull();
    expect(avatar?.querySelector("svg path")).not.toBeNull();
    expect(avatar?.textContent).not.toContain("◆");
  });

  it("renders an assistant text block as markdown", () => {
    const { container } = renderMsg({
      kind: "text",
      id: "m1",
      role: "assistant",
      text: "a **cube** is ready",
    });
    expect(container.querySelector(".msg-assistant")).not.toBeNull();
    expect(container.querySelector("strong")?.textContent).toBe("cube");
  });

  it("renders a user text block as a plain bubble", () => {
    const { container } = renderMsg({ kind: "text", id: "m2", role: "user", text: "make a cube" });
    expect(container.querySelector(".msg-user")).not.toBeNull();
    expect(container.textContent).toContain("make a cube");
  });

  it("renders clarification questions with quick-reply chips", () => {
    const { container, getByText } = renderMsg({
      kind: "clarification",
      id: "m6",
      questions: [
        { text: "Metric or imperial?", options: ["mm", "in"] },
        { text: "Through-hole or blind?" },
      ],
    });
    expect(container.querySelector(".msg-assistant")).not.toBeNull();
    expect(getByText("Metric or imperial?")).not.toBeNull();
    expect(getByText("Through-hole or blind?")).not.toBeNull();
    // Chips render for the question that declared options.
    expect(getByText("mm")).not.toBeNull();
    expect(getByText("in")).not.toBeNull();
  });

  it("sends the answer as a user message when a chip is clicked", () => {
    const chat = vi.fn();
    const appWithChat = { chat } as unknown as Parameters<typeof ChatMessage>[0]["app"];
    const { getByText } = renderWithProviders(
      <ChatMessage
        msg={{
          kind: "clarification",
          id: "m6",
          questions: [{ text: "Metric or imperial?", options: ["mm", "in"] }],
        }}
        app={appWithChat}
        onRetry={() => {}}
        onEdit={() => {}}
      />,
    );
    fireEvent.click(getByText("mm"));
    expect(chat).toHaveBeenCalledWith("mm");
  });

  it("renders a persisted thinking block as a collapsed 'Thought' pane", () => {
    const { container, getByText, queryByText } = renderMsg({
      kind: "thinking",
      id: "m8-t",
      text: "deep reasoning here",
    });
    // The pane has a summary toggle and is collapsed by default (text hidden).
    expect(container.querySelector(".thinking-pane")).not.toBeNull();
    expect(getByText(/Thought/)).not.toBeNull();
    expect(queryByText("deep reasoning here")).toBeNull();
  });

  it("expands the thinking pane to reveal the reasoning when toggled", () => {
    const { container, getByText } = renderMsg({
      kind: "thinking",
      id: "m8-t",
      text: "deep reasoning here",
    });
    fireEvent.click(container.querySelector(".thinking-toggle")!);
    expect(getByText("deep reasoning here")).not.toBeNull();
  });

  it("renders a persisted plan as an ordered list", () => {
    const { container, getByText } = renderMsg({
      kind: "plan",
      id: "m9-p",
      steps: ["base plate", "bolt circle", "fillets"],
    });
    expect(container.querySelector(".msg-assistant")).not.toBeNull();
    const ol = container.querySelector("ol.plan-list");
    expect(ol).not.toBeNull();
    expect(ol?.querySelectorAll("li").length).toBe(3);
    expect(getByText("base plate")).not.toBeNull();
    expect(getByText("fillets")).not.toBeNull();
  });

  it("renders the live-turn plan as an ordered list ahead of the action card", () => {
    const turn: LiveTurn = {
      text: "",
      thinking: "",
      codegen: "",
      plan: ["sketch", "extrude"],
      steers: [],
      stageEvents: [{ event: "start", intent: "a bracket", max_tries: 3 }],
      result: null,
      clarification: null,
      stopReason: null,
      error: null,
      done: false,
    };
    const { container } = renderMsg({ kind: "live-chat", id: "live-chat", turn });
    const ol = container.querySelector("ol.plan-list");
    expect(ol).not.toBeNull();
    expect(ol?.querySelectorAll("li").length).toBe(2);
    // The plan list precedes the staged-progress action card in the DOM.
    const progress = container.querySelector(".staged-progress, .progress");
    if (progress && ol) {
      expect(ol.compareDocumentPosition(progress) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    }
  });

  it("streams the thinking pane open while a live turn is in flight", () => {
    const turn: LiveTurn = {
      text: "",
      thinking: "thinking out loud",
      codegen: "",
      plan: null,
      steers: [],
      stageEvents: [],
      result: null,
      clarification: null,
      stopReason: null,
      error: null,
      done: false,
    };
    const { container, getByText } = renderMsg({ kind: "live-chat", id: "live-chat", turn });
    // In flight: the pane is open so the streamed reasoning is visible.
    expect(container.querySelector(".thinking-pane")).not.toBeNull();
    expect(getByText("thinking out loud")).not.toBeNull();
  });

  it("collapses the live thinking pane once the turn completes", () => {
    const turn: LiveTurn = {
      text: "answer",
      thinking: "thinking out loud",
      codegen: "",
      plan: null,
      steers: [],
      stageEvents: [],
      result: null,
      clarification: null,
      stopReason: "end_turn",
      error: null,
      done: true,
    };
    const { container, queryByText } = renderMsg({ kind: "live-chat", id: "live-chat", turn });
    expect(container.querySelector(".thinking-pane")).not.toBeNull();
    // Collapsed once done: the reasoning text is hidden behind the summary.
    expect(queryByText("thinking out loud")).toBeNull();
  });

  it("shows a streaming cursor while the reply streams, gone once done", () => {
    const streaming = renderMsg({
      kind: "live-chat", id: "live-chat", turn: liveTurn({ text: "Hello", done: false }),
    });
    expect(streaming.container.querySelector(".stream-cursor")).not.toBeNull();

    const settled = renderMsg({
      kind: "live-chat", id: "live-chat", turn: liveTurn({ text: "Hello", done: true }),
    });
    expect(settled.container.querySelector(".stream-cursor")).toBeNull();
  });

  it("streams codegen live then collapses it once the turn settles", () => {
    const code = "from build123d import *\nresult = Box(1, 1, 1)";
    const live = renderMsg({
      kind: "live-chat", id: "live-chat", turn: liveTurn({ codegen: code, done: false }),
    });
    // In flight: the codegen pane is open and the streamed code is visible.
    expect(live.container.querySelector(".codegen-pane")).not.toBeNull();
    expect(live.getByText(/Writing code/)).toBeInTheDocument();
    expect(live.container.querySelector(".codegen-code")?.textContent).toContain("Box(1, 1, 1)");
    live.unmount(); // avoid cross-render query bleed (RTL queries bind to document.body)

    // Settled: the pane collapses to a summary and the code is no longer rendered.
    const done = renderMsg({
      kind: "live-chat", id: "live-chat", turn: liveTurn({ codegen: code, done: true }),
    });
    expect(done.queryByText(/Writing code/)).toBeNull();
    expect(done.getByText(/Wrote 2 lines/)).toBeInTheDocument();
    expect(done.container.querySelector(".codegen-code")).toBeNull();
    // Re-expandable: clicking the summary shows the code again.
    fireEvent.click(done.getByText(/Wrote 2 lines/));
    expect(done.container.querySelector(".codegen-code")?.textContent).toContain("Box(1, 1, 1)");
  });

  it("shows a Thinking placeholder before any content arrives", () => {
    const { container } = renderMsg({
      kind: "live-chat", id: "live-chat", turn: liveTurn(),  // streaming, nothing yet
    });
    expect(container.querySelector(".chat-thinking")).not.toBeNull();
    expect(container.textContent).toContain("Thinking");

    // Once text starts streaming, the placeholder gives way to the reply + cursor.
    const withText = renderMsg({
      kind: "live-chat", id: "live-chat", turn: liveTurn({ text: "Sure" }),
    });
    expect(withText.container.querySelector(".chat-thinking")).toBeNull();
    expect(withText.container.querySelector(".stream-cursor")).not.toBeNull();
  });
});
