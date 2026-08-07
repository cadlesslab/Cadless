"""Provider-agnostic conversational agent loop.

The self-hosted tool-use loop — the heart of the assistant. It depends ONLY on
the :mod:`cadless.llm` seam (the :class:`~cadless.llm.provider.ChatProvider`
protocol + neutral types) and a small tool registry. It never imports a vendor
SDK (no ``boto3``, no ``anthropic``): every wire-format concern lives behind the
provider seam, so the same loop drives Bedrock, Anthropic, or the offline fake.

Flow of one user turn
---------------------
1. Assemble the request: extend the static CAD system prompt with assistant
   operating instructions; replay persisted chat as neutral ``messages``;
   summarize the current model (code + params) into a context block; append the
   new user message.
2. Stream a turn from the provider, accumulating text + ``tool_use`` blocks.
3. If the turn stopped with ``stop_reason == tool_use``: execute each requested
   tool, append the assistant's ``tool_use`` block(s) and the resulting
   ``tool_result`` block(s) to the running transcript, and loop.
4. Repeat until the model emits ``end_turn`` — or a hard cap trips.

Hard caps (a misbehaving model can never loop unboundedly):
  * ``max_tool_iters`` — tool round-trips per turn (default :data:`Settings`).
  * identical-tool-call debounce — the same ``(name, input)`` twice in a turn is
    refused with an error ``tool_result`` instead of re-executing.
  * same-stage convergence escalation — layered above the debounce:
    the repair loop's ``attempt_count``/``last_stage`` are surfaced in the tool
    result, and repeated failures at the same stage append guidance steering the
    model to ``ask_clarification`` instead of burning the repair budget.
  * per-turn token + wall-clock budget.

Tools (lean, high-impact) — wrappers over the EXISTING pipeline / reparametrize
primitive, reused unchanged:
  * ``generate_model(spec)`` — fresh build123d model via the pipeline.
  * ``edit_model(change)``   — refine the current code via the pipeline.
  * ``set_parameters(params)`` — deterministic re-run via ``reparametrize`` (no
    LLM, no pipeline call).

``run_turn`` returns the assembled neutral blocks for persistence.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from cadless.config import Settings, settings
from cadless.llm.provider import ChatProvider
from cadless.llm.types import (
    ContentBlock,
    Message,
    StreamEvent,
    ToolDef,
    TurnParams,
)
from cadless.params import apply_param_overrides, extract_params
from cadless.pipeline import GenerationResult, Pipeline
from cadless.system_prompt import SYSTEM_PROMPT

# --- tool registry ----------------------------------------------------------

GENERATE_MODEL = "generate_model"
EDIT_MODEL = "edit_model"
SET_PARAMETERS = "set_parameters"
ASK_CLARIFICATION = "ask_clarification"
SUBMIT_PLAN = "submit_plan"

# Hard cap: a single ``ask_clarification`` call may pose at most this many
# questions. Anything beyond is truncated (the model is told to ask sparingly).
MAX_CLARIFICATION_QUESTIONS = 3

# Hard cap: a single ``submit_plan`` call may list at most this many steps. The
# plan is meant to be a brief overview, not an exhaustive script.
MAX_PLAN_STEPS = 8


def build_tools() -> list[ToolDef]:
    """The three neutral tool definitions exposed to the model.

    Schemas are intentionally minimal — the model fills a single high-signal
    field per tool — because the heavy lifting happens in the wrapped pipeline.
    """
    return [
        ToolDef(
            name=GENERATE_MODEL,
            description=(
                "Generate a brand-new build123d model from a natural-language "
                "specification. Use when the user wants a fresh part (not an edit "
                "of the current one). Returns build result, metrics, and a render "
                "thumbnail."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "spec": {
                        "type": "string",
                        "description": "Full natural-language description of the part to build.",
                    }
                },
                "required": ["spec"],
            },
        ),
        ToolDef(
            name=EDIT_MODEL,
            description=(
                "Edit the CURRENT model to satisfy a change request (the current "
                "code is in your context). Makes a MINIMAL, surgical edit that "
                "preserves all existing geometry and structure — change only what "
                "the request requires, never rewrite or simplify the model from "
                "scratch. Use for incremental geometry/construction changes. For a "
                "pure dimensional change that an existing `params` value already "
                "expresses, prefer set_parameters instead. Returns build result, "
                "metrics, and a thumbnail."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "change": {
                        "type": "string",
                        "description": "The change to make, e.g. 'make the hole 8mm'.",
                    }
                },
                "required": ["change"],
            },
        ),
        ToolDef(
            name=SET_PARAMETERS,
            description=(
                "Deterministically re-run the current model with overridden named "
                "dimensions from its `params` block. No code is regenerated — use "
                "this for pure dimensional changes that the parameters already "
                "express (faster and exact). Returns the re-run result."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "params": {
                        "type": "object",
                        "description": "Map of parameter name -> new numeric/string/bool value.",
                    }
                },
                "required": ["params"],
            },
        ),
        ToolDef(
            name=ASK_CLARIFICATION,
            description=(
                "Ask the user one or more clarifying questions when the request is "
                "genuinely ambiguous and a stated default would not do. This ENDS "
                f"your turn: you will not act until the user replies. Ask at most "
                f"{MAX_CLARIFICATION_QUESTIONS} questions, only the essential ones — "
                "prefer sensible defaults over over-asking. Provide `options` for a "
                "question when a few choices cover the likely answers (rendered as "
                "quick-reply chips)."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "questions": {
                        "type": "array",
                        "description": (f"Up to {MAX_CLARIFICATION_QUESTIONS} questions to ask."),
                        "items": {
                            "type": "object",
                            "properties": {
                                "text": {
                                    "type": "string",
                                    "description": "The question to ask the user.",
                                },
                                "options": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": (
                                        "Optional quick-reply choices for this question."
                                    ),
                                },
                            },
                            "required": ["text"],
                        },
                    }
                },
                "required": ["questions"],
            },
        ),
        ToolDef(
            name=SUBMIT_PLAN,
            description=(
                "Submit a short ordered plan of the steps you are about to take, "
                "BEFORE calling generate_model/edit_model, for any non-trivial part "
                "(e.g. base plate -> bolt circle -> vented rotor -> fillets). This "
                "does NOT end your turn: after submitting the plan, call the action "
                "tool to carry it out. Skip the plan entirely for trivial requests. "
                f"List at most {MAX_PLAN_STEPS} concise steps."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "steps": {
                        "type": "array",
                        "description": (f"Up to {MAX_PLAN_STEPS} ordered, concise plan steps."),
                        "items": {"type": "string"},
                    }
                },
                "required": ["steps"],
            },
        ),
    ]


# --- tool execution context -------------------------------------------------

# A reparametrize callable: (code, overrides) -> result dict. Defaults to the
# pipeline-free :func:`_default_reparametrize` built on the existing
# ``apply_param_overrides`` primitive + the execution worker.
ReparametrizeFn = Callable[[str, dict], dict]


@dataclass
class ToolContext:
    """Everything the tools need to run, kept out of the vendor-agnostic loop.

    ``pipeline`` is the existing CAD :class:`~cadless.pipeline.Pipeline`
    (injectable for tests). ``current_code`` / ``current_params`` describe the
    model the conversation is currently about — threaded into ``edit_model`` and
    ``set_parameters`` and summarized into the system context block.

    ``grounding`` is the dynamic-RAG block retrieved once per turn at
    the async layer (where the KB store lives) and handed down to the sync agent
    loop. It is forwarded only to ``generate_model`` (fresh generation); the
    edit/refine path never sees it. ``None`` => legacy no-retrieval behavior.

    ``forge`` + ``forge_n`` are the per-turn forge-mode gate (C4). When
    ``forge`` is True AND ``forge_n > 1``, a FRESH ``generate_model`` fans out
    ``forge_n`` candidates and judges them (best-of-N) instead of a single
    ``pipeline.run``; the winner is returned as the normal payload and the losers
    are surfaced under ``payload["forge"]`` for the chat layer to persist as
    non-current candidate rows. ``edit_model`` / ``set_parameters`` never forge.
    Default off => byte-for-byte the legacy single-run path.
    """

    pipeline: Any = None
    reparametrize: ReparametrizeFn | None = None
    current_code: str | None = None
    current_params: dict = field(default_factory=dict)
    export_dir: str | None = None
    grounding: str | None = None
    forge: bool = False
    forge_n: int = 1
    # Live token sink for streaming codegen: when set, fresh
    # ``generate_model`` codegen deltas are pushed here AS the code is written
    # (the chat layer wires it to the SSE queue), instead of being collected into
    # the post-tool progress burst. ``None`` => codegen is not surfaced live.
    on_codegen: Callable[[str], None] | None = None

    def __post_init__(self) -> None:
        if self.pipeline is None:
            self.pipeline = Pipeline()
        if self.reparametrize is None:
            self.reparametrize = _default_reparametrize


def _default_reparametrize(code: str, overrides: dict) -> dict:
    """Deterministic, LLM-free re-run using the existing ``params`` primitive.

    Splices ``overrides`` into the script's ``params`` literal
    (:func:`cadless.params.apply_param_overrides`) and executes the edited
    script via the same sandbox worker the pipeline uses. No model call.
    """
    from cadless.worker import run_code

    new_code = apply_param_overrides(code, overrides)
    res = run_code(new_code)
    return {
        "ok": res.ok,
        "error": res.error,
        "code": new_code,
        "parameters": {**extract_params(code), **overrides},
        "volume": res.volume,
        "bbox": res.bbox,
        "glb_path": res.glb_path,
        "step_path": res.step_path,
        "stl_path": res.stl_path,
        "obj_path": res.obj_path,
    }


def _route_codegen(on_progress, on_codegen):
    """Wrap a pipeline ``on_progress`` so codegen deltas stream live.

    Codegen token events (``{"event": "codegen", "text": …}``) are forwarded to the
    live ``on_codegen`` sink the moment they arrive (the chat layer pushes them to
    the SSE queue), and are NOT added to the collected progress events that burst as
    ``tool_progress`` after the tool settles. Every other progress event flows to
    ``on_progress`` unchanged. With no ``on_codegen`` sink, codegen events are
    dropped (avoids hundreds of stage rows) and the rest pass through.
    """

    def _route(event: dict) -> None:
        if event.get("event") == "codegen":
            if on_codegen is not None:
                on_codegen(event.get("text", ""))
            return
        if on_progress is not None:
            on_progress(event)

    return _route


def _result_summary(result: GenerationResult) -> dict:
    """The result + metrics + thumbnail payload returned to the model as JSON.

    ``attempt_count`` and ``last_stage`` surface the internal repair
    loop's convergence state so the orchestrator can detect a repeated 'Nth
    failure at the same stage' cycle and escalate to ``ask_clarification`` instead
    of burning the repair budget on the same failure.
    """
    return {
        "ok": result.ok,
        "error": result.error,
        "code": result.code,
        "attempt_count": result.attempt_count,
        "last_stage": result.last_stage,
        "metrics": {
            "volume": result.volume,
            "bbox": list(result.bbox) if result.bbox else None,
            "parameters": result.parameters,
        },
        "thumbnail": result.glb_path,  # GLB render is the thumbnail artifact
    }


def _reparam_summary(out: dict) -> dict:
    bbox = out.get("bbox")
    return {
        "ok": out.get("ok"),
        "error": out.get("error"),
        "code": out.get("code"),
        "metrics": {
            "volume": out.get("volume"),
            "bbox": list(bbox) if bbox else None,
            "parameters": out.get("parameters"),
        },
        "thumbnail": out.get("glb_path"),
    }


def _normalize_questions(raw: Any) -> list[dict]:
    """Coerce a model-supplied ``questions`` payload to ``[{text, options?}]``.

    Drops malformed/empty entries, keeps only ``text`` + (optional, string-list)
    ``options``, and hard-caps the count at :data:`MAX_CLARIFICATION_QUESTIONS`.
    """
    out: list[dict] = []
    for q in raw if isinstance(raw, list) else []:
        if not isinstance(q, dict):
            continue
        text = q.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        question: dict[str, Any] = {"text": text}
        opts = q.get("options")
        if isinstance(opts, list):
            options = [str(o) for o in opts if isinstance(o, (str, int, float))]
            if options:
                question["options"] = options
        out.append(question)
        if len(out) >= MAX_CLARIFICATION_QUESTIONS:
            break
    return out


def _normalize_steps(raw: Any) -> list[str]:
    """Coerce a model-supplied ``steps`` payload to a clean ordered ``list[str]``.

    Drops non-string / blank entries, trims whitespace, and hard-caps the count at
    :data:`MAX_PLAN_STEPS` (the model is told to keep the plan brief).
    """
    out: list[str] = []
    for s in raw if isinstance(raw, list) else []:
        if not isinstance(s, str):
            continue
        step = s.strip()
        if not step:
            continue
        out.append(step)
        if len(out) >= MAX_PLAN_STEPS:
            break
    return out


# Human-readable labels per tool, surfaced in the ``tool_start`` UI event so the
# action card has a heading before the pipeline progress streams in.
_TOOL_LABELS = {
    GENERATE_MODEL: "Generating model",
    EDIT_MODEL: "Editing model",
    SET_PARAMETERS: "Updating parameters",
    ASK_CLARIFICATION: "Asking a question",
    SUBMIT_PLAN: "Planning",
}


def tool_label(name: str) -> str:
    """Friendly label for a tool name (falls back to the raw name)."""
    return _TOOL_LABELS.get(name, name)


# --- mid-run steering --------------------------------------------

# A steer source: polled by the loop at each iteration boundary. Returns the
# next pending steer message for the session, or ``None`` when none is queued.
# The agent stays vendor- and transport-agnostic: the router supplies a source
# backed by a session-keyed queue (see :class:`SessionSteerRegistry`).
SteerSource = Callable[[], "str | None"]


class ListSteerSource:
    """A trivial FIFO :data:`SteerSource` over a fixed list (tests / in-process).

    Each call pops the next queued message; returns ``None`` once drained, so a
    message is injected exactly once and steering can never extend the loop.
    """

    def __init__(self, messages: Sequence[str] | None = None) -> None:
        self._pending: list[str] = [m for m in (messages or []) if m]

    def add(self, message: str) -> None:
        if message:
            self._pending.append(message)

    def __call__(self) -> str | None:
        return self._pending.pop(0) if self._pending else None


class SessionSteerRegistry:
    """Thread-safe, session-keyed FIFO queues of pending steer messages.

    The chat router shares one process-wide registry: the in-flight `/chat` loop
    drains its session's queue at each iteration boundary (via :meth:`source_for`),
    while the steer endpoint enqueues into the same queue from another request
    thread. Empty queues are pruned so the registry doesn't grow unbounded.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._queues: dict[int, list[str]] = {}

    def enqueue(self, session_id: int, message: str) -> None:
        if not message:
            return
        with self._lock:
            self._queues.setdefault(session_id, []).append(message)

    def pop(self, session_id: int) -> str | None:
        with self._lock:
            queue = self._queues.get(session_id)
            if not queue:
                return None
            msg = queue.pop(0)
            if not queue:
                del self._queues[session_id]
            return msg

    def source_for(self, session_id: int) -> SteerSource:
        """A :data:`SteerSource` bound to ``session_id`` for one running turn."""
        return lambda: self.pop(session_id)


@dataclass
class StreamEventOut:
    """One neutral streaming event produced by :meth:`Agent.stream_turn`.

    ``kind`` is the neutral UI-event name (``turn_start``, ``text_delta``,
    ``tool_start``, ``tool_progress``, ``tool_result``, ``turn_end``); ``data`` is
    the event's payload. The router maps these onto SSE wire events and persists
    the turn — it never sees vendor wire formats.
    """

    kind: str
    data: dict = field(default_factory=dict)


# --- system prompt ----------------------------------------------------------

_OPERATING_INSTRUCTIONS = f"""\

---
You are operating as an interactive CAD assistant in a tool-use loop. Beyond
writing build123d code, you converse with the user: plan before acting, ask a
brief clarifying question when a request is ambiguous, and explain what you did
in plain language after a tool runs.

You have five tools. Choose the most precise one:
  * `{GENERATE_MODEL}(spec)` — build a brand-new model from scratch. Use when the
    user wants a different part, not a tweak of the current one.
  * `{EDIT_MODEL}(change)` — modify the CURRENT model (its code is given to you in
    the context block). Use for incremental geometry/construction changes. Make a
    surgical edit: change only what the request requires and keep all other
    geometry, structure, and parameters identical — never rewrite or simplify the
    model from scratch. A small request must yield a small diff.
  * `{SET_PARAMETERS}(params)` — re-run the current model with new values for its
    declared `params` dimensions. Prefer this for pure dimensional changes that
    the existing parameters already express: it is exact and needs no codegen.
  * `{ASK_CLARIFICATION}(questions)` — ask the user when the request is genuinely
    ambiguous and no sensible default applies. Calling it ENDS your turn until the
    user replies, so ask only the essential questions (at most
    {MAX_CLARIFICATION_QUESTIONS}) and prefer stated defaults over over-asking.
  * `{SUBMIT_PLAN}(steps)` — for a NON-TRIVIAL part, submit a short ordered plan
    (e.g. base plate -> bolt circle -> vented rotor -> fillets) BEFORE you call
    `{GENERATE_MODEL}`/`{EDIT_MODEL}`. This does NOT end your turn: call the action
    tool next to carry the plan out. Skip the plan for trivial requests (a simple
    cube, a single dimensional tweak) — it is optional, never required.

After a tool returns, read its result and either call another tool or give the
user a short final answer. Do not call a tool with identical arguments twice.
"""


def build_agent_system_prompt() -> str:
    """Extend (not replace) the static CAD system prompt with operating rules."""
    return SYSTEM_PROMPT + _OPERATING_INSTRUCTIONS


def _model_context_block(context: ToolContext) -> ContentBlock | None:
    """Summarize the current model (code + params) as a user-role text block.

    Returned as a leading context message so the model always knows the script it
    is editing and the dimensions it can re-parametrize. ``None`` when there is no
    current model yet (a fresh conversation).
    """
    if not context.current_code:
        return None
    params = context.current_params or extract_params(context.current_code or "")
    summary = (
        "Current model in this project (the script you are editing):\n\n"
        f"```python\n{context.current_code.strip()}\n```\n\n"
        f"Declared parameters: {json.dumps(params)}"
    )
    return ContentBlock.of_text(summary)


# --- the loop ---------------------------------------------------------------


@dataclass
class AgentResult:
    """Outcome of one user turn.

    ``blocks`` is the assembled neutral transcript produced *during this turn*
    (the model's text/tool_use blocks plus the tool_result blocks the loop fed
    back), ready to persist via the store's ``blocks_json`` column. ``messages``
    is the same content grouped into role-tagged :class:`Message`s.
    """

    blocks: list[ContentBlock] = field(default_factory=list)
    messages: list[Message] = field(default_factory=list)
    stop_reason: str = "end_turn"
    stopped_on_cap: bool = False
    tool_iters: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    # The questions of a terminal ``ask_clarification`` turn, set when
    # ``stop_reason == "clarification"``. Empty otherwise.
    clarification: list[dict] = field(default_factory=list)


# Why a turn ended awaiting the user's reply (``ask_clarification`` was called):
# a terminal, non-auto-continued stop reason distinct from ``end_turn``.
CLARIFICATION_STOP = "clarification"


def _drain_steer(steer: SteerSource | None) -> list[ContentBlock]:
    """Drain all currently-queued steer messages into neutral user text blocks.

    Polls the source until it returns ``None`` (FIFO order), so every message
    queued before this boundary is injected at once. Returns an empty list when
    there is no source or nothing pending — the common case, near-zero cost.
    """
    if steer is None:
        return []
    blocks: list[ContentBlock] = []
    while True:
        msg = steer()
        if not msg:
            break
        blocks.append(ContentBlock.of_text(msg))
    return blocks


def _find_clarification(tool_uses: Sequence[ContentBlock]) -> ContentBlock | None:
    """Return the first ``ask_clarification`` tool_use in a turn, if any."""
    for tu in tool_uses:
        if tu.name == ASK_CLARIFICATION:
            return tu
    return None


def _plan_result_block(tu: ContentBlock, steps: list[str]) -> ContentBlock:
    """Acknowledge a ``submit_plan`` call so the transcript stays well-formed.

    ``submit_plan`` is not a pipeline action — it only records an ordered plan — so
    its ``tool_result`` is a synthetic acknowledgement that lets the loop continue
    to the real action tool.
    """
    return ContentBlock.of_tool_result(
        tool_use_id=tu.id or "",
        content=json.dumps({"ok": True, "plan_steps": steps}),
    )


class _StreamedTurn:
    """Accumulates one provider ``stream_turn`` into neutral content + metadata."""

    def __init__(self) -> None:
        self.text_parts: list[str] = []
        self.blocks: list[ContentBlock] = []  # assistant blocks in emission order
        self.thinking: list[ContentBlock] = []  # verbatim thinking blocks
        self.tool_uses: list[ContentBlock] = []
        self.stop_reason: str = "end_turn"
        self.input_tokens: int = 0
        self.output_tokens: int = 0

    def consume(self, stream) -> None:
        for chunk in stream:
            event = chunk.event
            payload = chunk.payload
            if event == StreamEvent.TEXT_DELTA:
                self.text_parts.append(payload.get("text", ""))
            elif event == StreamEvent.THINKING_STOP:
                block = payload.get("block")
                if block is not None:
                    self.thinking.append(block)
            elif event == StreamEvent.TOOL_USE_STOP:
                block = payload.get("block") or ContentBlock.of_tool_use(
                    id=payload.get("id") or "",
                    name=payload.get("name") or "",
                    input=payload.get("input") or {},
                )
                self.tool_uses.append(block)
            elif event == StreamEvent.TURN_DELTA:
                self.stop_reason = str(payload.get("stop_reason", "end_turn"))
            elif event == StreamEvent.USAGE:
                self.input_tokens += int(payload.get("input_tokens", 0))
                self.output_tokens += int(payload.get("output_tokens", 0))
        self._assemble_blocks()

    def _assemble_blocks(self) -> None:
        # Assistant turn blocks in the order the provider replays them: thinking
        # first (must precede the text/tool_use it justifies), then text, then
        # tool_use blocks.
        self.blocks.extend(self.thinking)
        text = "".join(self.text_parts)
        if text:
            self.blocks.append(ContentBlock.of_text(text))
        self.blocks.extend(self.tool_uses)


class Agent:
    """The vendor-agnostic tool-use loop.

    Holds only a :class:`ChatProvider` + a model id + caps; all geometry concerns
    are injected via :class:`ToolContext` at ``run_turn`` time.
    """

    def __init__(
        self,
        provider: ChatProvider,
        *,
        model: str | None = None,
        config: Settings | None = None,
        max_tool_iters: int | None = None,
        token_budget: int | None = None,
        time_budget_secs: float | None = None,
    ) -> None:
        cfg = config or settings
        self._provider = provider
        self._model = model or cfg.orchestrator_model
        self._tools = build_tools()
        self._system = build_agent_system_prompt()
        self._max_tool_iters = (
            max_tool_iters if max_tool_iters is not None else cfg.agent_max_tool_iters
        )
        self._token_budget = token_budget if token_budget is not None else cfg.agent_token_budget
        self._time_budget = (
            time_budget_secs if time_budget_secs is not None else cfg.agent_time_budget_secs
        )
        self._same_stage_escalation = max(1, cfg.agent_same_stage_escalation)
        self._max_tokens = cfg.agent_max_tokens
        self._caps = provider.capabilities(self._model)

    def _turn_params(self) -> TurnParams:
        """Per-turn generation knobs, gated on the model's capabilities.

        Enable thinking only when the model supports it. While thinking is on we
        must NOT force ``tool_choice`` (extended thinking is incompatible with a
        forced tool choice), so tool selection is left to the model (``None``);
        otherwise we hint ``auto``. Vendor specifics (the Converse request field)
        stay in the adapter — the loop only flips neutral flags.
        """
        thinking = self._caps.supports_thinking
        tool_choice = None if thinking else "auto"
        # Ample output budget so a thinking+text+tool-call turn isn't truncated
        # mid-tool-call (which yields an empty tool input + max_tokens stop that the
        # loop would silently drop) —.
        return TurnParams(thinking=thinking, tool_choice=tool_choice, max_tokens=self._max_tokens)

    def run_turn(
        self,
        *,
        user_text: str,
        context: ToolContext,
        history: Sequence[Message] | None = None,
        steer: SteerSource | None = None,
    ) -> AgentResult:
        """Run the tool loop for one user message; return the new neutral blocks.

        ``steer`` (optional) is polled at each iteration boundary — between tool
        round-trips, before the next model call. A queued message is injected as
        a user turn so the next model call sees it, and persisted in order as a
        neutral ``text`` block. Steering is checked INSIDE the cap-guarded loop,
        so it can never bypass the tool-iter / token / time caps.
        """
        messages: list[Message] = list(history or [])
        ctx_block = _model_context_block(context)
        user_content: list[ContentBlock] = []
        if ctx_block is not None:
            user_content.append(ctx_block)
        user_content.append(ContentBlock.of_text(user_text))
        messages.append(Message(role="user", content=user_content))

        produced: list[ContentBlock] = []  # new blocks for persistence
        result = AgentResult()
        seen_calls: set[str] = set()
        stage_failures: dict[str, int] = {}
        start = time.monotonic()

        while True:
            # Boundary: drain any queued steer message into the conversation so
            # the upcoming model call sees it (and persist it in order).
            for block in _drain_steer(steer):
                messages.append(Message(role="user", content=[block]))
                produced.append(block)

            turn = _StreamedTurn()
            turn.consume(
                self._provider.stream_turn(
                    model=self._model,
                    system=self._system,
                    messages=messages,
                    tools=self._tools,
                    params=self._turn_params(),
                )
            )
            result.input_tokens += turn.input_tokens
            result.output_tokens += turn.output_tokens

            # Record the assistant turn (text + any tool_use blocks).
            messages.append(Message(role="assistant", content=turn.blocks))
            produced.extend(turn.blocks)

            if turn.stop_reason != "tool_use" or not turn.tool_uses:
                result.stop_reason = turn.stop_reason
                break

            # ask_clarification is terminal: end the turn awaiting the user's reply
            # (no auto-continue), persisting a neutral clarification block.
            clar = _find_clarification(turn.tool_uses)
            if clar is not None:
                questions = _normalize_questions((clar.input or {}).get("questions"))
                produced.append(ContentBlock.of_clarification(questions=questions))
                result.clarification = questions
                result.stop_reason = CLARIFICATION_STOP
                break

            # submit_plan records a neutral ``plan`` block (the ordered steps) and
            # is acknowledged with a synthetic tool_result; it is NOT a pipeline
            # action, so the loop continues to the action tool. Plan is optional —
            # turns that never call it simply skip this.
            plan_uses = [tu for tu in turn.tool_uses if tu.name == SUBMIT_PLAN]
            action_uses = [tu for tu in turn.tool_uses if tu.name != SUBMIT_PLAN]
            plan_results: list[ContentBlock] = []
            for tu in plan_uses:
                steps = _normalize_steps((tu.input or {}).get("steps"))
                produced.append(ContentBlock.of_plan(steps=steps))
                plan_results.append(_plan_result_block(tu, steps))

            # Cap: too many tool round-trips already? (planning is free of the cap.)
            if action_uses and result.tool_iters >= self._max_tool_iters:
                produced.extend(plan_results)
                if plan_results:
                    messages.append(Message(role="user", content=plan_results))
                result.stopped_on_cap = True
                result.stop_reason = "tool_use"
                produced.append(
                    ContentBlock.of_text("[agent] tool-iteration cap reached; stopping this turn.")
                )
                break

            if action_uses:
                result.tool_iters += 1
            tool_results = plan_results + self._dispatch_tools(
                action_uses, context, seen_calls, stage_failures
            )
            produced.extend(tool_results)
            messages.append(Message(role="user", content=tool_results))

            # Budget caps: stop *after* feeding results so the transcript is valid.
            if (
                result.input_tokens + result.output_tokens >= self._token_budget
                or time.monotonic() - start >= self._time_budget
            ):
                result.stopped_on_cap = True
                result.stop_reason = "tool_use"
                break

        result.blocks = produced
        result.messages = messages
        return result

    def stream_turn(
        self,
        *,
        user_text: str,
        context: ToolContext,
        history: Sequence[Message] | None = None,
        steer: SteerSource | None = None,
    ):
        """Run one user turn, yielding neutral :class:`StreamEventOut`s as it goes.

        Mirrors :meth:`run_turn` but emits UI-shaped events incrementally so the
        SSE layer can stream them: ``turn_start`` once, ``text_delta`` per text
        chunk, ``tool_start``/``tool_progress``/``tool_result`` around each tool
        call (pipeline ``stage`` events nest inside ``tool_progress``), then a
        single ``turn_end``. The final event carries the assembled neutral blocks
        so the caller can persist the turn.
        """
        messages: list[Message] = list(history or [])
        ctx_block = _model_context_block(context)
        user_content: list[ContentBlock] = []
        if ctx_block is not None:
            user_content.append(ctx_block)
        user_content.append(ContentBlock.of_text(user_text))
        messages.append(Message(role="user", content=user_content))

        produced: list[ContentBlock] = []
        result = AgentResult()
        seen_calls: set[str] = set()
        stage_failures: dict[str, int] = {}
        start = time.monotonic()

        yield StreamEventOut("turn_start")

        while True:
            # Boundary: drain queued steer messages, inject + persist them, and
            # surface each as a ``steer`` UI event so the transcript shows it.
            for block in _drain_steer(steer):
                messages.append(Message(role="user", content=[block]))
                produced.append(block)
                yield StreamEventOut("steer", {"text": block.text or ""})

            turn = _StreamedTurn()
            stream = self._provider.stream_turn(
                model=self._model,
                system=self._system,
                messages=messages,
                tools=self._tools,
                params=self._turn_params(),
            )
            # Inline accumulation so text deltas can be surfaced as they arrive.
            for chunk in stream:
                event = chunk.event
                payload = chunk.payload
                if event == StreamEvent.TEXT_DELTA:
                    text = payload.get("text", "")
                    turn.text_parts.append(text)
                    if text:
                        yield StreamEventOut("text_delta", {"text": text})
                elif event == StreamEvent.THINKING_DELTA:
                    text = payload.get("text", "")
                    if text:
                        yield StreamEventOut("thinking_delta", {"text": text})
                elif event == StreamEvent.THINKING_STOP:
                    block = payload.get("block")
                    if block is not None:
                        turn.thinking.append(block)
                elif event == StreamEvent.TOOL_USE_STOP:
                    block = payload.get("block") or ContentBlock.of_tool_use(
                        id=payload.get("id") or "",
                        name=payload.get("name") or "",
                        input=payload.get("input") or {},
                    )
                    turn.tool_uses.append(block)
                elif event == StreamEvent.TURN_DELTA:
                    turn.stop_reason = str(payload.get("stop_reason", "end_turn"))
                elif event == StreamEvent.USAGE:
                    turn.input_tokens += int(payload.get("input_tokens", 0))
                    turn.output_tokens += int(payload.get("output_tokens", 0))
            turn._assemble_blocks()

            result.input_tokens += turn.input_tokens
            result.output_tokens += turn.output_tokens
            messages.append(Message(role="assistant", content=turn.blocks))
            produced.extend(turn.blocks)

            if turn.stop_reason != "tool_use" or not turn.tool_uses:
                # A ``max_tokens`` stop means the turn was truncated — often mid
                # tool-call, so the action's input is empty and it never ran. Don't
                # end silently looking successful: surface a note.
                if turn.stop_reason == "max_tokens":
                    note = (
                        "\n\n_My response was cut off before I could finish that "
                        "step — please send the request again (a bit more specific "
                        "helps)._"
                    )
                    produced.append(ContentBlock.of_text(note))
                    yield StreamEventOut("text_delta", {"text": note})
                result.stop_reason = turn.stop_reason
                break

            # ask_clarification is terminal: emit a clarification event, persist a
            # neutral block, and end the turn awaiting the user's reply.
            clar = _find_clarification(turn.tool_uses)
            if clar is not None:
                questions = _normalize_questions((clar.input or {}).get("questions"))
                produced.append(ContentBlock.of_clarification(questions=questions))
                result.clarification = questions
                result.stop_reason = CLARIFICATION_STOP
                yield StreamEventOut("clarification", {"questions": questions})
                break

            # submit_plan: emit the ordered plan as a `plan` event (BEFORE any
            # tool_start / action card) and persist a neutral `plan` block, then
            # acknowledge it so the loop continues to the action tool. Optional —
            # trivial turns never call it and simply skip this.
            plan_uses = [tu for tu in turn.tool_uses if tu.name == SUBMIT_PLAN]
            action_uses = [tu for tu in turn.tool_uses if tu.name != SUBMIT_PLAN]
            tool_results: list[ContentBlock] = []
            for tu in plan_uses:
                steps = _normalize_steps((tu.input or {}).get("steps"))
                produced.append(ContentBlock.of_plan(steps=steps))
                yield StreamEventOut("plan", {"steps": steps})
                tool_results.append(_plan_result_block(tu, steps))

            if action_uses and result.tool_iters >= self._max_tool_iters:
                produced.extend(tool_results)
                if tool_results:
                    messages.append(Message(role="user", content=tool_results))
                result.stopped_on_cap = True
                result.stop_reason = "tool_use"
                produced.append(
                    ContentBlock.of_text("[agent] tool-iteration cap reached; stopping this turn.")
                )
                break

            if action_uses:
                result.tool_iters += 1
            for tu in action_uses:
                yield StreamEventOut(
                    "tool_start", {"tool": tu.name, "label": tool_label(tu.name or "")}
                )
                events: list[dict] = []
                block, payload = self._execute_streamed(
                    tu, context, seen_calls, stage_failures, on_progress=events.append
                )
                for ev in events:
                    yield StreamEventOut("tool_progress", {"stage": ev})
                tool_results.append(block)
                tool_result_data = {
                    "tool": tu.name,
                    "ok": bool(payload.get("ok")),
                    "metrics": payload.get("metrics"),
                    "thumbnail": payload.get("thumbnail"),
                    "code": payload.get("code"),
                    "error": payload.get("error"),
                }
                # Forge race set: carry the losing candidates through so
                # the chat layer can persist them as non-current candidate rows. It
                # holds internal GenerationResult objects (not JSON), so the SSE
                # layer maps it to persistence rather than emitting it on the wire.
                if payload.get("forge") is not None:
                    tool_result_data["forge"] = payload["forge"]
                yield StreamEventOut("tool_result", tool_result_data)
            produced.extend(tool_results)
            messages.append(Message(role="user", content=tool_results))

            if (
                result.input_tokens + result.output_tokens >= self._token_budget
                or time.monotonic() - start >= self._time_budget
            ):
                result.stopped_on_cap = True
                result.stop_reason = "tool_use"
                break

        result.blocks = produced
        result.messages = messages
        yield StreamEventOut("turn_end", {"stop_reason": result.stop_reason, "result": result})

    def _execute_streamed(
        self,
        tu: ContentBlock,
        context: ToolContext,
        seen_calls: set[str],
        stage_failures: dict[str, int],
        *,
        on_progress: Callable[[dict], None],
    ) -> tuple[ContentBlock, dict]:
        """Execute one tool with progress streaming; return (block, summary payload).

        The summary payload is the same ``_result_summary``/``_reparam_summary``
        dict the model sees, so the SSE layer can map it to a ``tool_result`` UI
        event and the router can persist a version from it.
        """
        signature = f"{tu.name}:{json.dumps(tu.input or {}, sort_keys=True)}"
        if signature in seen_calls:
            block = ContentBlock.of_tool_result(
                tool_use_id=tu.id or "",
                content=(
                    "Refused: identical tool call already executed this turn. "
                    "Use the previous result instead of repeating it."
                ),
                is_error=True,
            )
            return block, {"ok": False, "error": "duplicate tool call"}
        seen_calls.add(signature)
        block, payload = self._execute_one(tu, context, on_progress=on_progress)
        block = self._escalate_on_repeated_stage(block, payload, stage_failures)
        return block, payload

    def _dispatch_tools(
        self,
        tool_uses: Sequence[ContentBlock],
        context: ToolContext,
        seen_calls: set[str],
        stage_failures: dict[str, int],
    ) -> list[ContentBlock]:
        """Execute each requested tool, debouncing identical calls."""
        results: list[ContentBlock] = []
        for tu in tool_uses:
            signature = f"{tu.name}:{json.dumps(tu.input or {}, sort_keys=True)}"
            if signature in seen_calls:
                results.append(
                    ContentBlock.of_tool_result(
                        tool_use_id=tu.id or "",
                        content=(
                            "Refused: identical tool call already executed this "
                            "turn. Use the previous result instead of repeating it."
                        ),
                        is_error=True,
                    )
                )
                continue
            seen_calls.add(signature)
            block, payload = self._execute_one(tu, context)
            results.append(self._escalate_on_repeated_stage(block, payload, stage_failures))
        return results

    def _escalate_on_repeated_stage(
        self,
        block: ContentBlock,
        payload: dict,
        stage_failures: dict[str, int],
    ) -> ContentBlock:
        """Convergence/cycle awareness above the identical-tool-call debounce.

        A failed ``generate_model``/``edit_model`` result carries the repair loop's
        ``last_stage``. We count failures per stage across the turn; once
        the same stage has failed :attr:`_same_stage_escalation` times, we append
        guidance to the tool_result steering the model to ``ask_clarification``
        rather than burning more of the repair budget on the same failure. Unlike
        the debounce (which keys on identical ``(name, input)``), this fires even
        when the inputs differ but the model keeps hitting the same wall.

        Returns ``block`` unchanged unless escalation triggers.
        """
        if payload.get("ok", True):
            return block
        stage = payload.get("last_stage")
        if not stage:
            return block
        count = stage_failures.get(stage, 0) + 1
        stage_failures[stage] = count
        if count < self._same_stage_escalation:
            return block
        hint = (
            f"\n\n[agent] The repair loop has now failed {count} times at the "
            f"'{stage}' stage this turn without converging. Do NOT retry the same "
            f"build — call {ASK_CLARIFICATION} to ask the user for the detail "
            f"needed to break the cycle instead of burning the repair budget."
        )
        return ContentBlock.of_tool_result(
            tool_use_id=block.tool_use_id or "",
            content=(block.content or "") + hint,
            is_error=block.is_error,
        )

    def _execute_one(
        self,
        tu: ContentBlock,
        context: ToolContext,
        *,
        on_progress: Callable[[dict], None] | None = None,
    ) -> tuple[ContentBlock, dict]:
        """Execute one tool, returning its ``tool_result`` block + summary payload.

        ``on_progress`` (when given) receives the wrapped pipeline's progress
        events — the ``stage`` vocabulary the SSE layer nests inside
        ``tool_progress``. The summary payload is the same dict the model sees.
        """
        name = tu.name
        args = tu.input or {}
        try:
            if name == GENERATE_MODEL:
                # fresh generation gets the turn's RAG grounding; the
                # edit/refine path below deliberately does not (B4 scope).
                if context.forge and context.forge_n > 1:
                    # Forge mode (C4): race N candidates and judge them
                    # (C1 fan-out -> C2 select) instead of a single run. The winner
                    # is the normal payload; the losers ride along under
                    # payload["forge"] so the async chat layer can persist them as
                    # non-current candidate rows (it owns the store).
                    payload = self._forge_generate(args.get("spec", ""), context)
                    self._adopt(
                        context,
                        payload.get("code"),
                        (payload.get("metrics") or {}).get("parameters"),
                    )
                else:
                    res = context.pipeline.run(
                        args.get("spec", ""),
                        export_dir=context.export_dir,
                        on_progress=_route_codegen(on_progress, context.on_codegen),
                        grounding=context.grounding,
                    )
                    payload = _result_summary(res)
                    self._adopt(context, res.code, res.parameters)
            elif name == EDIT_MODEL:
                res = context.pipeline.run(
                    args.get("change", ""),
                    export_dir=context.export_dir,
                    prior_code=context.current_code,
                    on_progress=on_progress,
                )
                payload = _result_summary(res)
                self._adopt(context, res.code, res.parameters)
            elif name == SET_PARAMETERS:
                if not context.current_code:
                    raise ValueError("no current model to re-parametrize")
                out = context.reparametrize(context.current_code, args.get("params", {}))
                payload = _reparam_summary(out)
                if out.get("ok"):
                    self._adopt(context, out.get("code"), out.get("parameters"))
            else:
                block = ContentBlock.of_tool_result(
                    tool_use_id=tu.id or "",
                    content=f"Unknown tool: {name!r}",
                    is_error=True,
                )
                return block, {"ok": False, "error": f"unknown tool: {name!r}"}
        except Exception as exc:  # surface tool failure to the model, keep looping
            block = ContentBlock.of_tool_result(
                tool_use_id=tu.id or "",
                content=f"{type(exc).__name__}: {exc}",
                is_error=True,
            )
            return block, {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        # The forge race set rides on the payload for the chat layer to
        # persist, but it holds non-JSON GenerationResult objects and is internal —
        # keep it out of the tool_result content the model sees.
        model_view = {k: v for k, v in payload.items() if k != "forge"}
        block = ContentBlock.of_tool_result(
            tool_use_id=tu.id or "",
            content=json.dumps(model_view),
            is_error=not payload.get("ok", True),
        )
        return block, payload

    def _forge_generate(self, spec: str, context: ToolContext) -> dict:
        """Best-of-N fresh generation (C4): fan out -> judge -> summarize.

        Runs ``forge_n`` candidates in parallel (C1 ``run_candidates``) and selects
        the winner (C2 ``select_winner``). Returns the same summary dict a single
        ``generate_model`` produces (so the existing persist-as-current path is
        unchanged) plus a ``forge`` key carrying the losing candidates + the parent
        version for the chat layer to persist as non-current candidate rows.

        When the judge finds no passing candidate, the least-bad result is summarized
        as the (failing) payload and no losers are surfaced — nothing should be
        promoted over the existing model, so a failed race never replaces a working one.
        """
        from cadless.judge import select_winner

        candidates = context.pipeline.run_candidates(
            spec,
            n=context.forge_n,
            export_dir=context.export_dir,
            grounding=context.grounding,
        )
        judged = select_winner(candidates, intent=spec)
        win = judged.winner
        payload = (
            _result_summary(win)
            if win is not None
            else {
                "ok": False,
                "error": "forge: no candidates",
                "code": None,
                "metrics": {"volume": None, "bbox": None, "parameters": {}},
                "thumbnail": None,
            }
        )
        if win is not None and not judged.no_winner:
            payload["forge"] = {
                "losers": [c for c in candidates if c is not win],
                "winner": win,
            }
        return payload

    @staticmethod
    def _adopt(context: ToolContext, code: str | None, params: dict | None) -> None:
        """Make the just-built model the 'current' one for subsequent tools."""
        if code:
            context.current_code = code
            context.current_params = params or extract_params(code)
