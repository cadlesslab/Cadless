"""Tests for the provider-agnostic agent loop.

The loop depends ONLY on the ``cadless.llm`` seam + a tool registry, so every
test here is driven by an offline :class:`FakeChatProvider` script — no network,
no AWS, no real LLM. The CAD pipeline and the ``reparametrize`` primitive are
injected (or monkeypatched) so the agent's *control flow* (tool dispatch, caps,
debounce, block assembly) is what's under test, not build123d geometry.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence

from cadless.agent import (
    Agent,
    AgentResult,
    ListSteerSource,
    ToolContext,
    build_tools,
)
from cadless.llm.providers import StreamChunk
from cadless.llm.providers.fake import FakeChatProvider
from cadless.llm.types import (
    Capabilities,
    Message,
    StreamEvent,
    ToolDef,
    TurnParams,
)

# --- fakes -----------------------------------------------------------------


def _text_turn(text: str) -> list[StreamChunk]:
    """A scripted assistant turn that says ``text`` and stops (end_turn)."""
    return [
        StreamChunk(StreamEvent.TURN_START),
        StreamChunk(StreamEvent.TEXT_DELTA, {"text": text}),
        StreamChunk(StreamEvent.TURN_DELTA, {"stop_reason": "end_turn"}),
        StreamChunk(StreamEvent.USAGE, {"input_tokens": 10, "output_tokens": 5}),
        StreamChunk(StreamEvent.TURN_STOP),
    ]


def _thinking_then_text_turn(thinking: str, text: str) -> list[StreamChunk]:
    """A scripted turn that streams a reasoning delta + a thinking block, then text.

    Mirrors the bedrock adapter: a ``THINKING_DELTA`` (the streamed summary) and,
    at block stop, a neutral ``thinking`` :class:`ContentBlock` carrying
    ``provider``/``provider_raw`` for verbatim replay.
    """
    from cadless.llm.types import ContentBlock

    block = ContentBlock.of_thinking(
        thinking,
        provider="bedrock",
        provider_raw={
            "reasoningContent": {"reasoningText": {"text": thinking, "signature": "sig-xyz"}}
        },
    )
    return [
        StreamChunk(StreamEvent.TURN_START),
        StreamChunk(StreamEvent.THINKING_DELTA, {"text": thinking}),
        StreamChunk(StreamEvent.THINKING_STOP, {"text": thinking, "block": block}),
        StreamChunk(StreamEvent.TEXT_DELTA, {"text": text}),
        StreamChunk(StreamEvent.TURN_DELTA, {"stop_reason": "end_turn"}),
        StreamChunk(StreamEvent.USAGE, {"input_tokens": 10, "output_tokens": 5}),
        StreamChunk(StreamEvent.TURN_STOP),
    ]


def _tool_turn(*, tool_use_id: str, name: str, tool_input: dict) -> list[StreamChunk]:
    """A scripted assistant turn that calls a tool and stops (tool_use)."""
    from cadless.llm.types import ContentBlock

    block = ContentBlock.of_tool_use(id=tool_use_id, name=name, input=tool_input)
    return [
        StreamChunk(StreamEvent.TURN_START),
        StreamChunk(
            StreamEvent.TOOL_USE_STOP,
            {"id": tool_use_id, "name": name, "input": tool_input, "block": block},
        ),
        StreamChunk(StreamEvent.TURN_DELTA, {"stop_reason": "tool_use"}),
        StreamChunk(StreamEvent.USAGE, {"input_tokens": 10, "output_tokens": 5}),
        StreamChunk(StreamEvent.TURN_STOP),
    ]


class ScriptedProvider(FakeChatProvider):
    """A fake that returns a *different* scripted turn on each ``stream_turn`` call.

    The base ``FakeChatProvider`` replays one fixed script forever; a multi-turn
    agent loop needs the model's reply to change after it sees a tool_result, so
    this subclass pops the next turn-script from a queue.
    """

    def __init__(self, turns: Sequence[list[StreamChunk]]) -> None:
        super().__init__()
        self._turns = list(turns)
        self._i = 0

    def stream_turn(self, **kwargs) -> Iterator[StreamChunk]:
        # Snapshot messages — the agent mutates the live list across turns.
        kwargs = {**kwargs, "messages": list(kwargs["messages"])}
        self.calls.append(kwargs)
        turn = self._turns[min(self._i, len(self._turns) - 1)]
        self._i += 1
        yield from turn


class CapProvider(ScriptedProvider):
    """A scripted provider whose capabilities are configurable per test."""

    def __init__(self, turns: Sequence[list[StreamChunk]], *, caps: Capabilities) -> None:
        super().__init__(turns)
        self._caps = caps

    def capabilities(self, model: str) -> Capabilities:
        return self._caps


class LoopForeverProvider(FakeChatProvider):
    """A fake that asks for the same tool on every turn — never says end_turn."""

    def __init__(self, *, tool_input: dict) -> None:
        super().__init__()
        self._input = tool_input
        self._n = 0

    def stream_turn(self, **kwargs) -> Iterator[StreamChunk]:
        kwargs = {**kwargs, "messages": list(kwargs["messages"])}
        self.calls.append(kwargs)
        self._n += 1
        # Vary the tool_use id each turn so this isn't caught by the debounce —
        # only the iteration cap can stop it.
        yield from _tool_turn(
            tool_use_id=f"tu-{self._n}", name="generate_model", tool_input=self._input
        )


# --- tool-context spies -----------------------------------------------------


class SpyPipeline:
    """Stand-in for the CAD pipeline; records calls, returns a canned result."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []
        self.groundings: list[str | None] = []

    def run(self, intent, export_dir=None, on_progress=None, prior_code=None, grounding=None):
        from cadless.pipeline import GenerationResult

        self.calls.append((intent, prior_code))
        self.groundings.append(grounding)
        return GenerationResult(
            ok=True,
            intent=intent,
            code='params = {"size": 10}\nresult = None\n',
            volume=1000.0,
            bbox=(10, 10, 10),
            glb_path="/tmp/model.glb",
            parameters={"size": 10},
        )


def _context(pipeline=None, reparametrize=None, grounding=None) -> ToolContext:
    return ToolContext(
        pipeline=pipeline or SpyPipeline(),
        reparametrize=reparametrize,
        current_code='params = {"size": 10}\nresult = None\n',
        current_params={"size": 10},
        grounding=grounding,
    )


# --- (a) scripted tool_use -> tool_result -> end_turn -----------------------


def test_scripted_tool_use_then_end_turn_executes_tool_and_terminates():
    pipeline = SpyPipeline()
    provider = ScriptedProvider(
        [
            _tool_turn(
                tool_use_id="tu-1",
                name="generate_model",
                tool_input={"spec": "a 10mm cube"},
            ),
            _text_turn("Done — here's your cube."),
        ]
    )
    agent = Agent(provider=provider, model="fake-model")
    result = agent.run_turn(user_text="make a 10mm cube", context=_context(pipeline=pipeline))

    assert isinstance(result, AgentResult)
    # The tool actually ran against the pipeline.
    assert pipeline.calls == [("a 10mm cube", None)]
    # The loop terminated on end_turn (exactly 2 provider turns: tool, then text).
    assert len(provider.calls) == 2
    assert result.stop_reason == "end_turn"
    # Assembled blocks include the tool_use, its tool_result, and the final text.
    kinds = [b.kind for b in result.blocks]
    assert "tool_use" in kinds
    assert "tool_result" in kinds
    assert kinds[-1] == "text"
    assert "Done" in result.blocks[-1].text


# --- (b) iteration cap stops an infinite loop -------------------------------


def test_infinite_tool_loop_stopped_at_iteration_cap():
    pipeline = SpyPipeline()
    provider = LoopForeverProvider(tool_input={"spec": "a cube"})
    agent = Agent(provider=provider, model="fake-model", max_tool_iters=6)
    result = agent.run_turn(user_text="loop forever", context=_context(pipeline=pipeline))

    # Stopped at the cap, not by end_turn.
    assert result.stopped_on_cap is True
    # At most ``max_tool_iters`` tool executions happened.
    assert len(pipeline.calls) <= 6
    # The provider was not called unboundedly.
    assert len(provider.calls) <= 7


# --- (c) identical repeated tool calls are debounced ------------------------


def test_identical_repeated_tool_calls_are_debounced():
    pipeline = SpyPipeline()
    same = {"spec": "a 10mm cube"}
    provider = ScriptedProvider(
        [
            _tool_turn(tool_use_id="tu-1", name="generate_model", tool_input=same),
            _tool_turn(tool_use_id="tu-2", name="generate_model", tool_input=same),
            _text_turn("ok"),
        ]
    )
    agent = Agent(provider=provider, model="fake-model")
    result = agent.run_turn(user_text="cube", context=_context(pipeline=pipeline))

    # The pipeline ran only ONCE despite two identical tool calls.
    assert len(pipeline.calls) == 1
    # The second (debounced) call still produced a tool_result block (an error one)
    # so the transcript stays well-formed for the provider.
    tool_results = [b for b in result.blocks if b.kind == "tool_result"]
    assert len(tool_results) == 2
    assert any(b.is_error for b in tool_results)


# --- (d) set_parameters uses reparametrize, no model/pipeline call ----------


def test_set_parameters_uses_reparametrize_without_model_call():
    calls: list[dict] = []

    def fake_reparametrize(code: str, params: dict) -> dict:
        calls.append(params)
        return {
            "ok": True,
            "code": code,
            "parameters": {**{"size": 10}, **params},
            "volume": 8000.0,
            "bbox": (20, 20, 20),
            "glb_path": "/tmp/reparam.glb",
        }

    pipeline = SpyPipeline()
    provider = ScriptedProvider(
        [
            _tool_turn(
                tool_use_id="tu-1",
                name="set_parameters",
                tool_input={"params": {"size": 20}},
            ),
            _text_turn("resized"),
        ]
    )
    agent = Agent(provider=provider, model="fake-model")
    ctx = _context(pipeline=pipeline, reparametrize=fake_reparametrize)
    result = agent.run_turn(user_text="make it 20", context=ctx)

    # reparametrize was used with the override...
    assert calls == [{"size": 20}]
    # ...and the LLM-backed pipeline was NOT called for set_parameters.
    assert pipeline.calls == []
    assert result.stop_reason == "end_turn"


# --- registry + prompt assembly --------------------------------------------


def test_build_tools_registers_all_tools():
    tools = build_tools()
    names = {t.name for t in tools}
    assert names == {
        "generate_model",
        "edit_model",
        "set_parameters",
        "ask_clarification",
        "submit_plan",
    }
    assert all(isinstance(t, ToolDef) for t in tools)


def test_edit_model_tool_description_demands_surgical_preserving_edit():
    """the edit_model tool must steer the model to a minimal edit.

    Regression for the over-simplification bug — the tool description (which the
    model reads when choosing a tool) must say the edit is surgical/preserving and
    point pure-dimension changes at set_parameters.
    """
    edit = next(t for t in build_tools() if t.name == "edit_model")
    desc = edit.description.lower()
    assert "surgical" in desc or "minimal" in desc
    assert "preserve" in desc or "preserves" in desc or "identical" in desc
    assert "set_parameters" in desc


def test_operating_instructions_demand_surgical_edit_for_edit_model():
    """The system-prompt operating rules echo the surgical-edit mandate."""
    from cadless.agent import build_agent_system_prompt

    prompt = build_agent_system_prompt().lower()
    assert "surgical edit" in prompt
    assert "small request must yield a small diff" in prompt


def test_edit_model_passes_current_code_as_prior_code():
    pipeline = SpyPipeline()
    provider = ScriptedProvider(
        [
            _tool_turn(
                tool_use_id="tu-1",
                name="edit_model",
                tool_input={"change": "make the hole bigger"},
            ),
            _text_turn("edited"),
        ]
    )
    agent = Agent(provider=provider, model="fake-model")
    ctx = _context(pipeline=pipeline)
    agent.run_turn(user_text="edit it", context=ctx)

    # edit_model refines the *current* code (prior_code is threaded through).
    assert pipeline.calls == [("make the hole bigger", ctx.current_code)]
    # grounding is for FRESH generation only — never the refine path.
    assert pipeline.groundings == [None]


def test_generate_model_threads_context_grounding_into_pipeline():
    """ToolContext.grounding reaches pipeline.run for generate_model."""
    pipeline = SpyPipeline()
    provider = ScriptedProvider(
        [
            _tool_turn(
                tool_use_id="tu-1",
                name="generate_model",
                tool_input={"spec": "a 10mm cube"},
            ),
            _text_turn("done"),
        ]
    )
    agent = Agent(provider=provider, model="fake-model")
    ctx = _context(pipeline=pipeline, grounding="SOME GROUNDING")
    agent.run_turn(user_text="make a cube", context=ctx)

    assert pipeline.calls == [("a 10mm cube", None)]
    assert pipeline.groundings == ["SOME GROUNDING"]


# --- ask_clarification: blocking turn + 3-question cap ------------


def _clarify_turn(*, tool_use_id: str, questions: list[dict]) -> list[StreamChunk]:
    """A scripted assistant turn that calls ``ask_clarification`` and stops."""
    from cadless.llm.types import ContentBlock

    tool_input = {"questions": questions}
    block = ContentBlock.of_tool_use(id=tool_use_id, name="ask_clarification", input=tool_input)
    return [
        StreamChunk(StreamEvent.TURN_START),
        StreamChunk(
            StreamEvent.TOOL_USE_STOP,
            {
                "id": tool_use_id,
                "name": "ask_clarification",
                "input": tool_input,
                "block": block,
            },
        ),
        StreamChunk(StreamEvent.TURN_DELTA, {"stop_reason": "tool_use"}),
        StreamChunk(StreamEvent.USAGE, {"input_tokens": 10, "output_tokens": 5}),
        StreamChunk(StreamEvent.TURN_STOP),
    ]


def test_ask_clarification_ends_the_turn_and_does_not_continue():
    pipeline = SpyPipeline()
    # A second turn is scripted but must NEVER be consumed: clarification is
    # terminal — the loop ends awaiting the user's reply.
    provider = ScriptedProvider(
        [
            _clarify_turn(
                tool_use_id="tu-1",
                questions=[{"text": "Metric or imperial?", "options": ["mm", "in"]}],
            ),
            _text_turn("should not be reached"),
        ]
    )
    agent = Agent(provider=provider, model="fake-model")
    result = agent.run_turn(user_text="make a bolt", context=_context(pipeline=pipeline))

    # The loop ended the turn on the clarification (no auto-continue): exactly ONE
    # provider call, and the pipeline never ran.
    assert len(provider.calls) == 1
    assert pipeline.calls == []
    assert result.stop_reason == "clarification"


def test_ask_clarification_persists_a_clarification_block_with_questions():
    provider = ScriptedProvider(
        [
            _clarify_turn(
                tool_use_id="tu-1",
                questions=[
                    {"text": "Metric or imperial?", "options": ["mm", "in"]},
                    {"text": "Through-hole or blind?"},
                ],
            ),
        ]
    )
    agent = Agent(provider=provider, model="fake-model")
    result = agent.run_turn(user_text="make a bolt", context=_context())

    clar = [b for b in result.blocks if b.kind == "clarification"]
    assert len(clar) == 1
    questions = clar[0].input["questions"]
    assert [q["text"] for q in questions] == [
        "Metric or imperial?",
        "Through-hole or blind?",
    ]
    assert questions[0]["options"] == ["mm", "in"]


def test_ask_clarification_truncates_to_three_questions():
    five = [{"text": f"Q{i}?"} for i in range(5)]
    provider = ScriptedProvider([_clarify_turn(tool_use_id="tu-1", questions=five)])
    agent = Agent(provider=provider, model="fake-model")
    result = agent.run_turn(user_text="ambiguous", context=_context())

    clar = next(b for b in result.blocks if b.kind == "clarification")
    questions = clar.input["questions"]
    # Hard cap: at most 3 questions survive.
    assert len(questions) == 3
    assert [q["text"] for q in questions] == ["Q0?", "Q1?", "Q2?"]


def test_stream_turn_emits_clarification_event_and_ends_turn():
    provider = ScriptedProvider(
        [
            _clarify_turn(
                tool_use_id="tu-1",
                questions=[{"text": "Round or square?", "options": ["round", "square"]}],
            ),
            _text_turn("should not be reached"),
        ]
    )
    agent = Agent(provider=provider, model="fake-model")
    events = list(agent.stream_turn(user_text="make a tube", context=_context()))

    kinds = [e.kind for e in events]
    assert "clarification" in kinds
    clar = next(e for e in events if e.kind == "clarification")
    assert clar.data["questions"][0]["text"] == "Round or square?"
    assert clar.data["questions"][0]["options"] == ["round", "square"]
    end = next(e for e in events if e.kind == "turn_end")
    assert end.data["stop_reason"] == "clarification"
    # Terminal: only one provider turn consumed.
    assert len(provider.calls) == 1


def test_system_prompt_extends_cad_prompt_with_operating_instructions():
    from cadless.agent import build_agent_system_prompt
    from cadless.system_prompt import SYSTEM_PROMPT

    prompt = build_agent_system_prompt()
    # Extends, not replaces, the static CAD system prompt.
    assert SYSTEM_PROMPT in prompt
    # Mentions the tools so the model knows when to use each.
    assert "generate_model" in prompt
    assert "edit_model" in prompt
    assert "set_parameters" in prompt


def test_replay_persisted_blocks_are_sent_as_neutral_messages():
    from cadless.llm.types import ContentBlock

    prior = [
        Message(role="user", content=[ContentBlock.of_text("earlier request")]),
        Message(role="assistant", content=[ContentBlock.of_text("earlier reply")]),
    ]
    provider = ScriptedProvider([_text_turn("hi")])
    agent = Agent(provider=provider, model="fake-model")
    agent.run_turn(user_text="now this", history=prior, context=_context())

    sent: Sequence[Message] = provider.calls[0]["messages"]
    # History replayed first, then the new user turn.
    assert sent[0].content[0].text == "earlier request"
    assert sent[1].content[0].text == "earlier reply"
    assert sent[-1].role == "user"
    # The new user message's text block carries the user's request (a current-model
    # context block may precede it).
    assert "now this" in sent[-1].content[-1].text


def test_current_model_summarized_into_context_block():
    provider = ScriptedProvider([_text_turn("ok")])
    agent = Agent(provider=provider, model="fake-model")
    ctx = _context()
    agent.run_turn(user_text="what is the current model?", context=ctx)

    sent: Sequence[Message] = provider.calls[0]["messages"]
    blob = "\n".join(b.text or "" for m in sent for b in m.content if b.kind == "text")
    # The current code + params are summarized somewhere in the replayed context.
    assert "size" in blob
    assert 'params = {"size": 10}' in blob or "size" in blob


# --- submit_plan: ordered plan before acting ---------------------


def _plan_turn(*, tool_use_id: str, steps: list[str]) -> list[StreamChunk]:
    """A scripted assistant turn that calls ``submit_plan`` and stops (tool_use)."""
    from cadless.llm.types import ContentBlock

    tool_input = {"steps": steps}
    block = ContentBlock.of_tool_use(id=tool_use_id, name="submit_plan", input=tool_input)
    return [
        StreamChunk(StreamEvent.TURN_START),
        StreamChunk(
            StreamEvent.TOOL_USE_STOP,
            {
                "id": tool_use_id,
                "name": "submit_plan",
                "input": tool_input,
                "block": block,
            },
        ),
        StreamChunk(StreamEvent.TURN_DELTA, {"stop_reason": "tool_use"}),
        StreamChunk(StreamEvent.USAGE, {"input_tokens": 10, "output_tokens": 5}),
        StreamChunk(StreamEvent.TURN_STOP),
    ]


def test_submit_plan_is_registered_as_a_tool():
    names = {t.name for t in build_tools()}
    assert "submit_plan" in names


def test_submit_plan_persists_an_ordered_plan_block_and_continues():
    """A plan is NOT terminal: it persists a `plan` block, then the loop acts."""
    pipeline = SpyPipeline()
    provider = ScriptedProvider(
        [
            _plan_turn(tool_use_id="tu-0", steps=["base plate", "bolt circle"]),
            _tool_turn(
                tool_use_id="tu-1",
                name="generate_model",
                tool_input={"spec": "a flange"},
            ),
            _text_turn("Done — built your flange."),
        ]
    )
    agent = Agent(provider=provider, model="fake-model")
    result = agent.run_turn(user_text="make a flange", context=_context(pipeline=pipeline))

    # The plan did not end the turn: the action tool still ran afterwards.
    assert pipeline.calls == [("a flange", None)]
    assert result.stop_reason == "end_turn"

    plans = [b for b in result.blocks if b.kind == "plan"]
    assert len(plans) == 1
    assert plans[0].input["steps"] == ["base plate", "bolt circle"]

    # The plan block precedes the action tool_use (generate_model) that acts on it.
    plan_idx = next(i for i, b in enumerate(result.blocks) if b.kind == "plan")
    action_idx = next(
        i
        for i, b in enumerate(result.blocks)
        if b.kind == "tool_use" and b.name == "generate_model"
    )
    assert plan_idx < action_idx


def test_stream_turn_emits_plan_event_before_tool_start():
    pipeline = SpyPipeline()
    provider = ScriptedProvider(
        [
            _plan_turn(tool_use_id="tu-0", steps=["sketch", "extrude", "fillet"]),
            _tool_turn(
                tool_use_id="tu-1",
                name="generate_model",
                tool_input={"spec": "a bracket"},
            ),
            _text_turn("done"),
        ]
    )
    agent = Agent(provider=provider, model="fake-model")
    events = list(
        agent.stream_turn(user_text="make a bracket", context=_context(pipeline=pipeline))
    )
    kinds = [e.kind for e in events]

    assert "plan" in kinds
    plan = next(e for e in events if e.kind == "plan")
    assert plan.data["steps"] == ["sketch", "extrude", "fillet"]
    # Ordering: the plan event is emitted BEFORE the action card's tool_start.
    assert kinds.index("plan") < kinds.index("tool_start")


def test_plan_is_optional_trivial_turn_omits_it_without_error():
    """A trivial turn that never calls submit_plan produces no plan block/event."""
    pipeline = SpyPipeline()
    provider = ScriptedProvider(
        [
            _tool_turn(
                tool_use_id="tu-1",
                name="generate_model",
                tool_input={"spec": "a cube"},
            ),
            _text_turn("done"),
        ]
    )
    agent = Agent(provider=provider, model="fake-model")
    events = list(agent.stream_turn(user_text="make a cube", context=_context(pipeline=pipeline)))
    assert all(e.kind != "plan" for e in events)
    result = next(e for e in events if e.kind == "turn_end").data["result"]
    assert all(b.kind != "plan" for b in result.blocks)
    assert result.stop_reason == "end_turn"


def test_submit_plan_drops_blank_steps():
    provider = ScriptedProvider(
        [
            _plan_turn(tool_use_id="tu-0", steps=["real", "  ", "", "second"]),
            _tool_turn(
                tool_use_id="tu-1",
                name="generate_model",
                tool_input={"spec": "x"},
            ),
            _text_turn("done"),
        ]
    )
    agent = Agent(provider=provider, model="fake-model")
    result = agent.run_turn(user_text="x", context=_context())
    plan = next(b for b in result.blocks if b.kind == "plan")
    assert plan.input["steps"] == ["real", "second"]


def test_provider_protocol_only_imports_no_vendor_sdk():
    """The agent module must not *import* boto3 or anthropic (only the llm seam).

    Prose in docstrings may name the vendors; what's forbidden is a real import,
    so we inspect the module's AST import nodes rather than the raw source text.
    """
    import ast
    import inspect

    import cadless.agent as agent_mod

    tree = ast.parse(inspect.getsource(agent_mod))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "boto3" not in imported
    assert "anthropic" not in imported


def test_capabilities_gate_does_not_break_offline_run():
    # A provider with permissive capabilities runs cleanly offline.
    provider = ScriptedProvider([_text_turn("ok")])
    assert isinstance(provider.capabilities("fake-model"), Capabilities)
    agent = Agent(provider=provider, model="fake-model")
    out = agent.run_turn(user_text="hi", context=_context())
    assert out.stop_reason == "end_turn"
    # The agent passed neutral TurnParams + tools to the provider.
    assert isinstance(provider.calls[0]["params"], TurnParams)
    assert all(isinstance(t, ToolDef) for t in provider.calls[0]["tools"])


# --- thinking: capability gating + tool_choice constraint --------


def test_thinking_requested_only_when_capabilities_support_it():
    """``params.thinking`` is True iff the model reports ``supports_thinking``."""
    yes = CapProvider([_text_turn("ok")], caps=Capabilities(supports_thinking=True))
    Agent(provider=yes, model="m").run_turn(user_text="hi", context=_context())
    assert yes.calls[0]["params"].thinking is True

    no = CapProvider([_text_turn("ok")], caps=Capabilities(supports_thinking=False))
    Agent(provider=no, model="m").run_turn(user_text="hi", context=_context())
    assert no.calls[0]["params"].thinking is False


def test_tool_choice_not_forced_while_thinking_is_on():
    """While thinking is on, ``tool_choice`` is NOT forced (no 'any' constraint).

    Forcing tool_choice is incompatible with extended thinking, so the loop must
    leave it unset (``None``) when thinking is enabled, even though tools are
    offered.
    """
    provider = CapProvider(
        [_text_turn("ok")],
        caps=Capabilities(supports_thinking=True, supports_tool_choice=True),
    )
    Agent(provider=provider, model="m").run_turn(user_text="hi", context=_context())
    params = provider.calls[0]["params"]
    assert params.thinking is True
    assert params.tool_choice != "any"
    assert params.tool_choice is None


def test_streamed_thinking_block_round_trips_verbatim_via_provider_raw():
    """A ``thinking`` block is persisted with its provider_raw replayed unchanged.

    The block the agent records must carry the EXACT ``provider_raw`` the provider
    emitted (signature included) — never reserialized from ``text``.
    """
    provider = ScriptedProvider([_thinking_then_text_turn("reasoning here", "answer")])
    result = Agent(provider=provider, model="m").run_turn(user_text="hi", context=_context())
    thinking = [b for b in result.blocks if b.kind == "thinking"]
    assert len(thinking) == 1
    blk = thinking[0]
    assert blk.provider == "bedrock"
    # provider_raw preserved byte-for-byte (signature intact), NOT rebuilt.
    assert blk.provider_raw == {
        "reasoningContent": {"reasoningText": {"text": "reasoning here", "signature": "sig-xyz"}}
    }


def test_stream_turn_surfaces_thinking_delta_events():
    """``Agent.stream_turn`` emits a ``thinking_delta`` UI event per reasoning chunk."""
    provider = ScriptedProvider([_thinking_then_text_turn("pondering", "answer")])
    agent = Agent(provider=provider, model="m")
    events = list(agent.stream_turn(user_text="hi", context=_context()))
    deltas = [e for e in events if e.kind == "thinking_delta"]
    assert deltas and deltas[0].data["text"] == "pondering"
    # The persisted blocks (carried on turn_end) include the verbatim thinking block.
    end = [e for e in events if e.kind == "turn_end"][-1]
    blocks = end.data["result"].blocks
    assert any(b.kind == "thinking" and b.provider == "bedrock" for b in blocks)


# --- mid-run message queuing / steer -----------------------------


def test_steer_message_injected_at_next_iteration_boundary():
    """A steer message queued mid-turn is injected before the next model call.

    The provider runs three iterations (two tool calls, then text). A steer
    source yields one message; the loop must inject it as a user turn at a
    boundary, so a *later* provider call sees it in its ``messages``.
    """
    pipeline = SpyPipeline()
    provider = ScriptedProvider(
        [
            _tool_turn(tool_use_id="tu-1", name="generate_model", tool_input={"spec": "a"}),
            _tool_turn(tool_use_id="tu-2", name="edit_model", tool_input={"change": "b"}),
            _text_turn("done"),
        ]
    )
    # The steer message "arrives" only after the first boundary poll, modelling a
    # user who types mid-stream (the registry-backed source behaves the same way).
    polls = {"n": 0}

    def steer() -> str | None:
        polls["n"] += 1
        if polls["n"] == 2:
            return "actually make it red"
        return None

    agent = Agent(provider=provider, model="fake-model")
    result = agent.run_turn(
        user_text="make a part", context=_context(pipeline=pipeline), steer=steer
    )

    # The injected steer text reached the model: some later provider call's
    # messages contain a user block carrying the steer text.
    def _has_steer(call) -> bool:
        for msg in call["messages"]:
            if msg.role != "user":
                continue
            for block in msg.content:
                if block.kind == "text" and "actually make it red" in (block.text or ""):
                    return True
        return False

    assert any(_has_steer(c) for c in provider.calls)
    # The steer message is persisted in order as a neutral user text block,
    # appearing AFTER the first tool_result and BEFORE the final assistant text.
    steer_idx = next(
        i
        for i, b in enumerate(result.blocks)
        if b.kind == "text" and "actually make it red" in (b.text or "")
    )
    first_tool_result_idx = next(i for i, b in enumerate(result.blocks) if b.kind == "tool_result")
    last_text_idx = max(
        i for i, b in enumerate(result.blocks) if b.kind == "text" and "done" in (b.text or "")
    )
    assert first_tool_result_idx < steer_idx < last_text_idx
    assert result.stop_reason == "end_turn"


def test_steer_source_drained_once_consumed():
    """A steer message is consumed exactly once (not re-injected every boundary)."""
    pipeline = SpyPipeline()
    provider = ScriptedProvider(
        [
            _tool_turn(tool_use_id="tu-1", name="generate_model", tool_input={"spec": "a"}),
            _tool_turn(tool_use_id="tu-2", name="edit_model", tool_input={"change": "b"}),
            _text_turn("done"),
        ]
    )
    steer = ListSteerSource(["one steer only"])
    agent = Agent(provider=provider, model="fake-model")
    result = agent.run_turn(user_text="go", context=_context(pipeline=pipeline), steer=steer)
    injected = [b for b in result.blocks if b.kind == "text" and "one steer only" in (b.text or "")]
    assert len(injected) == 1


def test_steer_does_not_bypass_iteration_cap():
    """Steering must never let a turn exceed the hard tool-iteration cap."""
    pipeline = SpyPipeline()
    provider = LoopForeverProvider(tool_input={"spec": "a cube"})
    # A steer source that keeps offering messages forever must NOT extend the loop.
    steer = ListSteerSource(["steer"] * 100)
    agent = Agent(provider=provider, model="fake-model", max_tool_iters=4)
    result = agent.run_turn(user_text="loop", context=_context(pipeline=pipeline), steer=steer)
    assert result.stopped_on_cap is True
    assert len(pipeline.calls) <= 4


def test_stream_turn_emits_steer_event_when_injected():
    """``Agent.stream_turn`` surfaces an injected steer message as a UI event."""
    provider = ScriptedProvider(
        [
            _tool_turn(tool_use_id="tu-1", name="generate_model", tool_input={"spec": "a"}),
            _text_turn("done"),
        ]
    )
    steer = ListSteerSource(["steer me"])
    agent = Agent(provider=provider, model="fake-model")
    events = list(agent.stream_turn(user_text="go", context=_context(), steer=steer))
    steered = [e for e in events if e.kind == "steer"]
    assert steered and steered[0].data["text"] == "steer me"


# --- attempt-count + stage surfacing / same-stage escalation ------


class FailingPipeline:
    """Stand-in pipeline whose every run fails at a fixed final stage.

    Records calls and returns a multi-attempt :class:`GenerationResult` whose
    last attempt failed at ``stage`` — the shape the repair loop produces when it
    burns its budget without converging.
    """

    def __init__(self, *, stage: str = "execute", attempts: int = 3) -> None:
        self._stage = stage
        self._attempts = attempts
        self.calls: list[tuple[str, str | None]] = []

    def run(self, intent, export_dir=None, on_progress=None, prior_code=None, grounding=None):
        from cadless.pipeline import Attempt, GenerationResult

        self.calls.append((intent, prior_code))
        atts = [
            Attempt(n=i + 1, code="bad", stage=self._stage, error=f"{self._stage} boom")
            for i in range(self._attempts)
        ]
        return GenerationResult(
            ok=False,
            intent=intent,
            code="bad",
            error=f"{self._stage}: boom",
            attempts=atts,
        )


def test_result_summary_surfaces_attempt_count_and_last_stage():
    from cadless.agent import _result_summary
    from cadless.pipeline import Attempt, GenerationResult

    result = GenerationResult(
        ok=False,
        intent="a widget",
        code="bad",
        error="execution: boom",
        attempts=[
            Attempt(n=1, code="bad", stage="validate", error="validation: boom"),
            Attempt(n=2, code="bad", stage="execute", error="execution: boom"),
            Attempt(n=3, code="bad", stage="execute", error="execution: boom"),
        ],
    )
    summary = _result_summary(result)

    assert summary["attempt_count"] == 3
    # The last attempt's stage is surfaced so the orchestrator knows WHERE it failed.
    assert summary["last_stage"] == "execute"


def test_successful_result_surfaces_attempt_count_and_execute_stage():
    from cadless.agent import _result_summary
    from cadless.pipeline import Attempt, GenerationResult

    result = GenerationResult(
        ok=True,
        intent="a cube",
        code="good",
        volume=1000.0,
        bbox=(10, 10, 10),
        attempts=[Attempt(n=1, code="good", stage="execute", error=None)],
    )
    summary = _result_summary(result)

    assert summary["ok"] is True
    assert summary["attempt_count"] == 1
    assert summary["last_stage"] == "execute"


def test_repeated_same_stage_failure_escalates_to_ask_clarification():
    """A 2nd failed generate/edit at the SAME stage adds an escalation hint.

    Layered above the identical-tool-call debounce: the inputs DIFFER each call
    (so the debounce never fires), yet the agent still notices convergence on the
    same failing stage and steers the model toward ``ask_clarification`` instead
    of burning more of the repair budget.
    """
    pipeline = FailingPipeline(stage="execute")
    provider = ScriptedProvider(
        [
            _tool_turn(
                tool_use_id="tu-1", name="generate_model", tool_input={"spec": "attempt one"}
            ),
            _tool_turn(
                tool_use_id="tu-2", name="generate_model", tool_input={"spec": "attempt two"}
            ),
            _text_turn("giving up"),
        ]
    )
    agent = Agent(provider=provider, model="fake-model")
    result = agent.run_turn(user_text="make it", context=_context(pipeline=pipeline))

    # Both calls really ran (distinct inputs -> debounce did NOT fire).
    assert len(pipeline.calls) == 2
    tool_results = [b for b in result.blocks if b.kind == "tool_result"]
    assert len(tool_results) == 2
    # The 2nd same-stage failure carries the escalation guidance.
    assert "ask_clarification" in (tool_results[1].content or "")
    assert "execute" in (tool_results[1].content or "")
    # The 1st failure does NOT (only one occurrence so far).
    assert "ask_clarification" not in (tool_results[0].content or "")


def test_distinct_failing_stages_do_not_escalate():
    """Failures at DIFFERENT stages are not a convergence cycle — no escalation."""
    provider = ScriptedProvider(
        [
            _tool_turn(tool_use_id="tu-1", name="generate_model", tool_input={"spec": "one"}),
            _tool_turn(tool_use_id="tu-2", name="edit_model", tool_input={"change": "two"}),
            _text_turn("done"),
        ]
    )
    agent = Agent(provider=provider, model="fake-model")

    # First call fails at "validate", second at "execute".
    pipelines = iter([FailingPipeline(stage="validate"), FailingPipeline(stage="execute")])

    class SwitchingPipeline:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str | None]] = []

        def run(self, intent, export_dir=None, on_progress=None, prior_code=None, grounding=None):
            self.calls.append((intent, prior_code))
            return next(pipelines).run(
                intent, export_dir, on_progress, prior_code, grounding=grounding
            )

    result = agent.run_turn(user_text="make it", context=_context(pipeline=SwitchingPipeline()))
    tool_results = [b for b in result.blocks if b.kind == "tool_result"]
    assert len(tool_results) == 2
    assert all("ask_clarification" not in (b.content or "") for b in tool_results)


# --- max_tokens truncation ---------------------------------------


def _truncated_tool_turn(*, tool_use_id: str, name: str) -> list[StreamChunk]:
    """A turn that emits a tool_use but is cut off (empty input, max_tokens stop)."""
    from cadless.llm.types import ContentBlock

    block = ContentBlock.of_tool_use(id=tool_use_id, name=name, input={})
    return [
        StreamChunk(StreamEvent.TURN_START),
        StreamChunk(StreamEvent.THINKING_DELTA, {"text": "lots of reasoning about slabs"}),
        StreamChunk(StreamEvent.TEXT_DELTA, {"text": "I'll align the second-floor slab."}),
        StreamChunk(
            StreamEvent.TOOL_USE_STOP,
            {"id": tool_use_id, "name": name, "input": {}, "block": block},
        ),
        StreamChunk(StreamEvent.TURN_DELTA, {"stop_reason": "max_tokens"}),
        StreamChunk(StreamEvent.USAGE, {"input_tokens": 10, "output_tokens": 5}),
        StreamChunk(StreamEvent.TURN_STOP),
    ]


def test_orchestrator_turn_requests_large_max_tokens():
    """The agent asks for ample output so a thinking+tool turn isn't truncated."""
    from cadless.config import settings

    provider = ScriptedProvider([_text_turn("hi")])
    agent = Agent(provider=provider, model="fake-model")
    list(agent.stream_turn(user_text="hello", context=_context(pipeline=SpyPipeline())))
    assert provider.calls[0]["params"].max_tokens == settings.agent_max_tokens


def test_max_tokens_truncated_tool_turn_surfaces_note_and_skips_tool():
    """A cut-off tool call (empty input, max_tokens) must NOT silently no-op: the
    tool doesn't run and the user is told the response was truncated."""
    pipeline = SpyPipeline()
    provider = ScriptedProvider([_truncated_tool_turn(tool_use_id="tu-1", name="edit_model")])
    agent = Agent(provider=provider, model="fake-model")
    events = list(
        agent.stream_turn(user_text="align the floors", context=_context(pipeline=pipeline))
    )
    # The truncated tool call never executed.
    assert pipeline.calls == []
    assert not any(e.kind == "tool_result" for e in events)
    # The turn surfaces a "cut off" note rather than ending as a silent success.
    text = "".join(e.data.get("text", "") for e in events if e.kind == "text_delta")
    assert "cut off" in text
    assert any(e.kind == "turn_end" for e in events)
