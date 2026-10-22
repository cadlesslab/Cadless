import { fireEvent, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { Version } from "../api";
import { ToastProvider } from "../components";
import { StoreProvider } from "../state";
import type { AppState } from "../store";
import { renderWithProviders } from "../test/utils";
import { ChatMessage } from "./ChatMessage";
import type { CritiqueRound, LiveTurn, ChatMessage as Msg } from "./chatModel";

function okVersion(): Version {
  return {
    id: 7, project_id: 1, prompt: "a cube", code: "result=...", ok: true, error: null,
    volume: 1, bbox: [1, 1, 1], created_at: "", parameters: {}, parent_version_id: null,
    plan_step: null, artifacts: [],
  };
}

// The component only invokes `app` inside event handlers we never trigger here.
const app = {} as Parameters<typeof ChatMessage>[0]["app"];

function msgElement(msg: Msg) {
  return <ChatMessage msg={msg} app={app} onRetry={() => {}} onEdit={() => {}} />;
}

function renderMsg(msg: Msg, state: Partial<AppState> = {}) {
  return renderWithProviders(msgElement(msg), state);
}

function imageMsg(over: Partial<Extract<Msg, { kind: "image" }>> = {}): Msg {
  return {
    kind: "image",
    id: "m12-i0",
    role: "user",
    messageId: 12,
    index: 0,
    mediaType: "image/png",
    reading: null,
    ...over,
  };
}

function liveTurn(over: Partial<LiveTurn> = {}): LiveTurn {
  return {
    text: "", thinking: "", codegen: "", plan: null, steers: [], stageEvents: [],
    critique: null, result: null, clarification: null, stopReason: null, error: null, done: false,
    findings: [],
    ...over,
  };
}

function critiqueRound(over: Partial<CritiqueRound> = {}): CritiqueRound {
  return {
    attempt: 1,
    matches: false,
    feedback: "The bore is on the wrong face.",
    views: [
      { name: "front", png_b64: "Zm9udA==" },
      { name: "right", png_b64: "cmlnaHQ=" },
      { name: "top", png_b64: "dG9w" },
      { name: "iso", png_b64: "aXNv" },
    ],
    ...over,
  };
}

describe("ChatMessage", () => {
  it("renders the CadlessIcon solid as the assistant avatar", () => {
    const { container } = renderMsg({ kind: "assistant", id: "a7", version: okVersion() });
    const avatar = container.querySelector(".msg-avatar");
    expect(avatar).not.toBeNull();
    expect(avatar?.querySelector("svg path")).not.toBeNull();
    expect(avatar?.textContent).not.toContain("◆");
  });

  it("writes a house's recorded size and volume on its result card in metres", () => {
    const house = { ...okVersion(), volume: 158.058e9, bbox: [12400, 8400, 6900] as [number, number, number] };
    const { container } = renderMsg({ kind: "assistant", id: "a7", version: house });
    expect(container.querySelector(".result-metrics")?.textContent).toBe(
      "158.1 m³ · 12.4 × 8.4 × 6.9 m",
    );
  });

  it("offers no way to open the code from a result card", () => {
    const { container, queryByText } = renderMsg({ kind: "assistant", id: "a7", version: okVersion() });
    expect(queryByText(/code/i)).toBeNull();
    expect(container.innerHTML).not.toContain("result=...");
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

  it("renders an assembly guide with its parts, steps and drawings", () => {
    const { container, getByText } = renderMsg({
      kind: "guide",
      id: "m9-g",
      versionId: 42,
      parts: ["base", "arm"],
      steps: ["Start with base.", "Fit arm to base."],
      frames: 2,
    });
    expect(container.querySelector("ol.plan-list")?.querySelectorAll("li").length).toBe(2);
    expect(getByText("Fit arm to base.")).not.toBeNull();
    const drawings = container.querySelectorAll(".assembly-guide-frames img");
    expect(drawings.length).toBe(2);
    // Addressed by index against the version, not by a URL kept in the
    // transcript, which would outlive the route that served it.
    expect(drawings[1].getAttribute("src")).toContain("/versions/42/artifacts/guide/1");
  });

  it("shows a stored turn's assembly findings, failures apart from the undecided", () => {
    const failure = "part 1 and part 2 share 40 mm^3 of interior volume";
    const undecided = "assembly order: part 2 and part 3 touch where the check cannot follow";
    const { getByRole } = renderMsg({
      kind: "assembly-findings",
      id: "m21-a0",
      failures: [failure],
      unchecked: [undecided],
    });
    const box = getByRole("group", { name: "Assembly check" });
    within(box).getByText("On a build that was not kept.");
    expect(within(box).getByRole("list", { name: "Not accepted" }).textContent).toBe(failure);
    expect(within(box).getByRole("list", { name: "Could not be established" }).textContent).toBe(
      undecided,
    );
  });

  it("leaves out a heading whose list is empty", () => {
    const { queryByText } = renderMsg({
      kind: "assembly-findings",
      id: "m21-a0",
      failures: ["part 1 does not fit the build volume"],
      unchecked: [],
    });
    expect(queryByText("Could not be established")).toBeNull();
  });

  it("shows each of a live turn's assembly findings, in order", () => {
    const { getAllByRole } = renderMsg({
      kind: "live-chat",
      id: "live-chat",
      turn: liveTurn({
        findings: [
          { failures: ["part 2 is trapped by part 1"], unchecked: [] },
          { failures: ["part 3 does not fit the build volume"], unchecked: [] },
        ],
      }),
    });
    const boxes = getAllByRole("group", { name: "Assembly check" });
    expect(boxes.map((b) => within(b).getByRole("list").textContent)).toEqual([
      "part 2 is trapped by part 1",
      "part 3 does not fit the build volume",
    ]);
  });

  it("shows no assembly check on a live turn with no findings", () => {
    const { queryByRole } = renderMsg({
      kind: "live-chat",
      id: "live-chat",
      turn: liveTurn({ result: { versionId: 12, ok: true, error: null }, findings: [] }),
    });
    expect(queryByRole("group", { name: "Assembly check" })).toBeNull();
  });

  it("renders an assembly guide that has no drawings", () => {
    // A build whose release headings went unmeasured still has an order worth
    // reading, so the steps must not be gated on there being pictures.
    const { container, getByText } = renderMsg({
      kind: "guide",
      id: "m3-g",
      versionId: 7,
      parts: [],
      steps: ["Start with part 1."],
      frames: 0,
    });
    expect(getByText("Start with part 1.")).not.toBeNull();
    expect(container.querySelectorAll(".assembly-guide-frames img").length).toBe(0);
  });

  it("renders the live-turn plan as an ordered list ahead of the action card", () => {
    const turn: LiveTurn = {
      text: "",
      thinking: "",
      codegen: "",
      plan: ["sketch", "extrude"],
      steers: [],
      stageEvents: [{ event: "start", intent: "a bracket", max_tries: 3 }],
      critique: null,
      result: null,
      findings: [],
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
      critique: null,
      result: null,
      findings: [],
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
      critique: null,
      result: null,
      findings: [],
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

  describe("codegen status", () => {
    // A streamed reply: a fence, then commented code, the newest comment
    // indented, closed by a full stop and trailed by spaces.
    const code = [
      "```python",
      "from build123d import *",
      "# Base plate with rounded corners",
      "plate = Box(40, 40, 4)",
      "    # Mounting holes through the plate.  ",
      "result = plate",
      "",
    ].join("\n");
    const codegenMsg = (codegen: string, done = false): Msg => ({
      kind: "live-chat", id: "live-chat", turn: liveTurn({ codegen, done }),
    });
    const phrase = (el: Element) => el.querySelector(".codegen-phrase")?.textContent;

    // Attributes included: no line of code may reach the DOM at all, not merely
    // stay out of the visible text.
    const holdsCode = (el: Element) =>
      ["build123d", "Box(40", "result =", "```"].some((s) => el.innerHTML.includes(s));

    it("shows one status line while codegen streams", () => {
      const { container } = renderMsg(codegenMsg(code));
      // The "Thinking…" placeholder must not sit beside it as a second line.
      const lines = container.querySelectorAll('[role="status"]');
      expect(lines).toHaveLength(1);
      expect(lines[0]).toHaveClass("codegen-status");
      expect(lines[0].querySelector("button")).toBeNull();
    });

    it("keeps every line of the streamed code out of the DOM", () => {
      const { container } = renderMsg(codegenMsg(code));
      expect(holdsCode(container)).toBe(false);
    });

    it("says what is being built in the model's words, from its newest comment", () => {
      const { container } = renderMsg(codegenMsg(code));
      expect(phrase(container)).toBe("Mounting holes through the plate");
    });

    it("reads a wrapped comment from its first line", () => {
      const wrapped = `${code}# Crank web joining the journals,\n# offset 5 mm from the pin\nweb = plate\n`;
      const { container } = renderMsg(codegenMsg(wrapped));
      expect(phrase(container)).toBe("Crank web joining the journals");
    });

    it("reads a ruled heading for its words, not its rules", () => {
      const ruled = `${code}# ----------\n# --- Ledge tabs ---\n# ----------\ntabs = plate\n`;
      const { container } = renderMsg(codegenMsg(ruled));
      expect(phrase(container)).toBe("Ledge tabs");
    });

    it("reads a heading drawn with box-drawing rules for its words", () => {
      const ruled = `${code}# ──────────\n# ── Tablet rest faces ────────\ntabs = plate\n`;
      const { container } = renderMsg(codegenMsg(ruled));
      expect(phrase(container)).toBe("Tablet rest faces");
    });

    it("keeps a comment block whole across a bare # separator", () => {
      const parted = `${code}# Hinge knuckle\n#\n# offset 2 mm for clearance\nknuckle = plate\n`;
      const { container } = renderMsg(codegenMsg(parted));
      expect(phrase(container)).toBe("Hinge knuckle");
    });

    it("leaves a comment out until its line is finished", () => {
      const { container } = renderMsg(codegenMsg(`${code}# Fillet the to`));
      expect(phrase(container)).toBe("Mounting holes through the plate");
    });

    it("says it is writing code until the model writes a comment", () => {
      const { container } = renderMsg(codegenMsg("```python\nfrom build123d import *\n"));
      expect(phrase(container)).toBe("Writing code");
    });

    // The same instance taking the next stretch of the stream, as a live turn does.
    function advance(live: ReturnType<typeof renderMsg>, msg: Msg) {
      live.rerender(
        <StoreProvider store={live.store}>
          <ToastProvider>{msgElement(msg)}</ToastProvider>
        </StoreProvider>,
      );
    }

    it("follows the stream as the next comment arrives", () => {
      const live = renderMsg(codegenMsg(code));
      advance(live, codegenMsg(`${code}# Fillet the top edges\n`));
      expect(phrase(live.container)).toBe("Fillet the top edges");
    });

    it("announces one fixed phrase in the live region and keeps the changing one from it", () => {
      const { container } = renderMsg(codegenMsg(code));
      const live = container.querySelector('[role="status"]');
      expect(live?.querySelector(".visually-hidden")?.textContent).toBe("Writing code");
      expect(live?.querySelector(".codegen-phrase")?.getAttribute("aria-hidden")).toBe("true");
    });

    const settled = "from build123d import *\nresult = Box(1, 1, 1)";

    it("settles to a fixed line count with no way to open the code", () => {
      const live = renderMsg(codegenMsg(code));
      advance(live, codegenMsg(settled, true));
      const status = live.container.querySelector(".codegen-status");
      expect(status?.textContent).toBe("Wrote 2 lines");
      expect(status?.querySelector("button")).toBeNull();
      expect(holdsCode(live.container)).toBe(false);
      fireEvent.click(status as Element);
      expect(holdsCode(live.container)).toBe(false);
    });

    it("announces the settled count from the live region already on screen", () => {
      // A region that appears with its text already in it is often not read out.
      const live = renderMsg(codegenMsg(code));
      const region = live.container.querySelector('[role="status"]');
      advance(live, codegenMsg(settled, true));
      expect(live.container.querySelector('[role="status"]')).toBe(region);
    });
  });

  it("fetches an attached picture from the attachment route, not from the block", () => {
    // The transcript hands back an image block with its `data` emptied, so a
    // renderer that inlined the payload would show nothing on every reload.
    const { container } = renderMsg(imageMsg({ index: 1 }), { activeProjectId: 3 });
    const img = container.querySelector("img");
    expect(img?.getAttribute("src")).toMatch(/\/projects\/3\/messages\/12\/attachments\/1$/);
    expect(img?.getAttribute("src")).not.toContain("base64");
  });

  it("puts the attached picture on the user's side of the thread", () => {
    // Under the assistant avatar it would read as something the model produced.
    const { container } = renderMsg(imageMsg(), { activeProjectId: 3 });
    expect(container.querySelector(".msg-user img")).not.toBeNull();
    expect(container.querySelector(".msg-assistant")).toBeNull();
  });

  it("uses the model's reading of the picture as its alt text", () => {
    const { container } = renderMsg(
      imageMsg({ reading: "a hand-drawn L-bracket with two holes" }),
      { activeProjectId: 3 },
    );
    expect(container.querySelector("img")?.getAttribute("alt")).toBe(
      "a hand-drawn L-bracket with two holes",
    );
  });

  it("names the kind of file in the alt text when no reading was written", () => {
    const { container } = renderMsg(imageMsg(), { activeProjectId: 3 });
    expect(container.querySelector("img")?.getAttribute("alt")).toBe(
      "Attached image (image/png)",
    );
  });

  it("gives the critique its own step in the staged progress list", () => {
    const { getByText } = renderMsg({
      kind: "live-chat",
      id: "live-chat",
      turn: liveTurn({
        stageEvents: [{ event: "stage", phase: "critique", status: "begin", attempt: 2 }],
      }),
    });
    expect(getByText("Review")).not.toBeNull();
  });

  it("keeps a reviewer's render out of the user's bubble", () => {
    // In the user bubble it reads as a reference the user supplied, which inverts
    // who made the part — the one thing the picture is there to show.
    const { container } = renderMsg(
      imageMsg({ role: "assistant", reading: "seen from the front" }),
      { activeProjectId: 3 },
    );
    expect(container.querySelector(".msg-user")).toBeNull();
    expect(container.querySelector(".msg-assistant img")).not.toBeNull();
  });

  it("describes a reviewer's render without calling it an attachment", () => {
    const { container } = renderMsg(imageMsg({ role: "assistant" }), { activeProjectId: 3 });
    expect(container.querySelector("img")?.getAttribute("alt")).toBe(
      "A render of the part this turn built (image/png)",
    );
  });

  it("renders a live critique round's views inline, each named in its alt text", () => {
    const { container } = renderMsg({
      kind: "live-chat", id: "live-chat", turn: liveTurn({ critique: critiqueRound() }),
    });
    const imgs = [...container.querySelectorAll(".critique-view")];
    expect(imgs.map((i) => i.getAttribute("alt"))).toEqual([
      "The part as built, seen from the front",
      "The part as built, seen from the right",
      "The part as built, seen from the top",
      "The part as built, seen from the iso",
    ]);
    // Live captures arrive as bytes, so there is no attachment route to ask.
    expect(imgs[0].getAttribute("src")).toBe("data:image/png;base64,Zm9udA==");
  });

  it("writes the reviewer's verdict as text rather than leaving it to colour", () => {
    const failed = renderMsg({
      kind: "live-chat", id: "live-chat", turn: liveTurn({ critique: critiqueRound({ attempt: 2 }) }),
    });
    const caption = failed.container.querySelector(".critique-verdict");
    expect(caption?.textContent).toBe("Review of attempt 2: The bore is on the wrong face.");
    failed.unmount(); // avoid cross-render query bleed (RTL queries bind to document.body)

    const matched = renderMsg({
      kind: "live-chat",
      id: "live-chat",
      turn: liveTurn({ critique: critiqueRound({ attempt: 3, matches: true, feedback: "" }) }),
    });
    expect(matched.container.querySelector(".critique-verdict")?.textContent).toBe(
      "Review of attempt 3: The render matches the request.",
    );
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

  it("drops the Thinking placeholder once a critique round is on screen", () => {
    // Stage events arrive in one burst after the tool settles, so mid-loop the
    // reviewer's renders are the only thing the turn has to show for itself.
    const { container } = renderMsg({
      kind: "live-chat", id: "live-chat", turn: liveTurn({ critique: critiqueRound() }),
    });
    expect(container.querySelector(".critique")).not.toBeNull();
    expect(container.querySelector(".chat-thinking")).toBeNull();
  });
});
