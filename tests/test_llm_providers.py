"""Tests for the bedrock + anthropic + fake ChatProviders.

The real adapters are fed *synthetic* vendor event streams (no network), so
these tests run offline. The genuinely-live smoke tests are marked with their
provider name (``bedrock``/``anthropic``/``openai``) and skipped by the default
``pytest -m "not bedrock and not anthropic and not openai"`` run.
"""

from __future__ import annotations

import os

import pytest

from cadless.config import Settings
from cadless.llm.provider import ChatProvider, EmbeddingsUnsupported
from cadless.llm.providers import StreamChunk
from cadless.llm.providers.anthropic import AnthropicChatProvider
from cadless.llm.providers.bedrock import BedrockChatProvider
from cadless.llm.providers.fake import FakeChatProvider
from cadless.llm.providers.openai import OpenAIChatProvider
from cadless.llm.registry import available_providers, build_provider
from cadless.llm.types import (
    Capabilities,
    ContentBlock,
    Message,
    StopReason,
    StreamEvent,
    ToolDef,
    TurnParams,
)

# --- registry wiring -------------------------------------------------------


def test_both_providers_registered():
    names = available_providers()
    assert "bedrock" in names
    assert "fake" in names


def test_build_bedrock_and_fake_from_registry():
    assert isinstance(build_provider("fake", settings=Settings()), ChatProvider)
    assert isinstance(build_provider("bedrock", settings=Settings()), ChatProvider)


def test_registry_autoloads_bundled_providers():
    # build_provider must work in a fresh interpreter without the caller
    # importing the providers package first — the registry loads it on demand.
    import subprocess
    import sys

    code = (
        "from cadless.llm.registry import build_provider, available_providers; "
        "from cadless.config import Settings; "
        "p = build_provider('bedrock', settings=Settings()); "
        "assert type(p).__name__ == 'BedrockChatProvider'; "
        "assert set(available_providers()) >= {'bedrock', 'fake'}; "
        "print('ok')"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert "ok" in out.stdout


# --- fake provider ---------------------------------------------------------


def test_fake_replays_scripted_chunks_in_order():
    script = [
        StreamChunk(StreamEvent.TURN_START),
        StreamChunk(StreamEvent.TEXT_DELTA, {"text": "hello "}),
        StreamChunk(StreamEvent.TEXT_DELTA, {"text": "world"}),
        StreamChunk(StreamEvent.TURN_STOP),
    ]
    provider = FakeChatProvider(script=script)
    out = list(
        provider.stream_turn(model="m", system="s", messages=[], tools=[], params=TurnParams())
    )
    assert [c.event for c in out] == [
        StreamEvent.TURN_START,
        StreamEvent.TEXT_DELTA,
        StreamEvent.TEXT_DELTA,
        StreamEvent.TURN_STOP,
    ]
    assert out[1].payload == {"text": "hello "}


def test_fake_replays_full_event_vocabulary():
    # text, thinking, tool_use, tool_result, stop — the AC's required set.
    script = [
        StreamChunk(StreamEvent.TURN_START),
        StreamChunk(StreamEvent.THINKING_DELTA, {"text": "let me think"}),
        StreamChunk(StreamEvent.TEXT_DELTA, {"text": "answer"}),
        StreamChunk(StreamEvent.TOOL_USE_START, {"id": "t1", "name": "calc"}),
        StreamChunk(StreamEvent.TOOL_INPUT_DELTA, {"partial_json": '{"x":'}),
        StreamChunk(StreamEvent.TOOL_INPUT_DELTA, {"partial_json": "1}"}),
        StreamChunk(StreamEvent.TOOL_USE_STOP),
        StreamChunk(StreamEvent.TURN_DELTA, {"stop_reason": StopReason.TOOL_USE}),
        StreamChunk(StreamEvent.TURN_STOP),
    ]
    provider = FakeChatProvider(script=script)
    out = list(
        provider.stream_turn(model="m", system="s", messages=[], tools=[], params=TurnParams())
    )
    assert [c.event for c in out] == [c.event for c in script]


def test_fake_is_deterministic_across_runs():
    script = [
        StreamChunk(StreamEvent.TURN_START),
        StreamChunk(StreamEvent.TEXT_DELTA, {"text": "x"}),
        StreamChunk(StreamEvent.TURN_STOP),
    ]
    provider = FakeChatProvider(script=script)
    first = [
        c.event
        for c in provider.stream_turn(
            model="m", system="s", messages=[], tools=[], params=TurnParams()
        )
    ]
    second = [
        c.event
        for c in provider.stream_turn(
            model="m", system="s", messages=[], tools=[], params=TurnParams()
        )
    ]
    assert first == second


def test_fake_complete_concatenates_text_deltas():
    script = [
        StreamChunk(StreamEvent.TURN_START),
        StreamChunk(StreamEvent.TEXT_DELTA, {"text": "abc"}),
        StreamChunk(StreamEvent.TEXT_DELTA, {"text": "def"}),
        StreamChunk(StreamEvent.TURN_STOP),
    ]
    provider = FakeChatProvider(script=script)
    assert provider.complete(model="m", system="s", user="u") == "abcdef"


def test_fake_satisfies_chatprovider_protocol():
    provider: ChatProvider = FakeChatProvider(script=[StreamChunk(StreamEvent.TURN_STOP)])
    assert isinstance(provider, ChatProvider)
    assert provider.capabilities("m").supports_thinking is True


# --- bedrock adapter (synthetic stream, no network) ------------------------


class _FakeStreamingClient:
    """Stand-in for boto3 bedrock-runtime; replays a scripted Converse stream."""

    def __init__(self, events):
        self._events = events
        self.calls: list[dict] = []

    def converse_stream(self, **kwargs):
        self.calls.append(kwargs)
        return {"stream": iter(self._events)}


def _text_stream(*texts):
    """A minimal Converse event stream emitting one text content block."""
    evs = [
        {"messageStart": {"role": "assistant"}},
        {"contentBlockStart": {"start": {}, "contentBlockIndex": 0}},
    ]
    for t in texts:
        evs.append({"contentBlockDelta": {"delta": {"text": t}, "contentBlockIndex": 0}})
    evs += [
        {"contentBlockStop": {"contentBlockIndex": 0}},
        {"messageStop": {"stopReason": "end_turn"}},
        {"metadata": {"usage": {"inputTokens": 7, "outputTokens": 3}}},
    ]
    return evs


def _provider_with(events):
    client = _FakeStreamingClient(events)
    return BedrockChatProvider(config=Settings(), client=client), client


def test_bedrock_maps_text_stream_to_neutral_events():
    provider, _ = _provider_with(_text_stream("hi ", "there"))
    out = list(
        provider.stream_turn(
            model="sonnet-4-6",
            system="s",
            messages=[Message(role="user", content=[ContentBlock.of_text("q")])],
            tools=[],
            params=TurnParams(),
        )
    )
    events = [c.event for c in out]
    assert events[0] == StreamEvent.TURN_START
    assert StreamEvent.TEXT_DELTA in events
    assert StreamEvent.TURN_DELTA in events
    assert events[-1] == StreamEvent.TURN_STOP
    text = "".join(c.payload["text"] for c in out if c.event == StreamEvent.TEXT_DELTA)
    assert text == "hi there"


def test_bedrock_resolves_slug_to_inference_profile():
    provider, client = _provider_with(_text_stream("x"))
    list(
        provider.stream_turn(
            model="sonnet-4-6", system="s", messages=[], tools=[], params=TurnParams()
        )
    )
    assert client.calls[0]["modelId"] == "us.anthropic.claude-sonnet-4-6"


def test_bedrock_maps_reasoning_to_thinking_delta():
    events = [
        {"messageStart": {"role": "assistant"}},
        {"contentBlockStart": {"start": {}, "contentBlockIndex": 0}},
        {
            "contentBlockDelta": {
                "delta": {"reasoningContent": {"text": "thinking..."}},
                "contentBlockIndex": 0,
            }
        },
        {"contentBlockStop": {"contentBlockIndex": 0}},
        {"messageStop": {"stopReason": "end_turn"}},
    ]
    provider, _ = _provider_with(events)
    out = list(
        provider.stream_turn(
            model="sonnet-4-6", system="s", messages=[], tools=[], params=TurnParams()
        )
    )
    think = [c for c in out if c.event == StreamEvent.THINKING_DELTA]
    assert think and think[0].payload["text"] == "thinking..."


def test_bedrock_accumulates_reasoning_into_thinking_block_with_signature():
    """A reasoning content block is emitted as a neutral ``thinking`` block whose
    ``provider_raw`` carries the verbatim reasoningContent (text + signature) for
    lossless replay back to Bedrock."""
    events = [
        {"messageStart": {"role": "assistant"}},
        {"contentBlockStart": {"start": {}, "contentBlockIndex": 0}},
        {
            "contentBlockDelta": {
                "delta": {"reasoningContent": {"text": "let me "}},
                "contentBlockIndex": 0,
            }
        },
        {
            "contentBlockDelta": {
                "delta": {"reasoningContent": {"text": "think"}},
                "contentBlockIndex": 0,
            }
        },
        {
            "contentBlockDelta": {
                "delta": {"reasoningContent": {"signature": "abc123"}},
                "contentBlockIndex": 0,
            }
        },
        {"contentBlockStop": {"contentBlockIndex": 0}},
        {"messageStop": {"stopReason": "end_turn"}},
    ]
    provider, _ = _provider_with(events)
    out = list(
        provider.stream_turn(
            model="sonnet-4-6", system="s", messages=[], tools=[], params=TurnParams()
        )
    )
    # Deltas streamed for the live pane.
    deltas = [c for c in out if c.event == StreamEvent.THINKING_DELTA]
    assert "".join(c.payload["text"] for c in deltas) == "let me think"
    # A THINKING_STOP carries the accumulated neutral block for persistence.
    stops = [c for c in out if c.event == StreamEvent.THINKING_STOP]
    assert len(stops) == 1
    block: ContentBlock = stops[0].payload["block"]
    assert block.kind == "thinking"
    assert block.text == "let me think"
    assert block.provider == "bedrock"
    # provider_raw is the full reasoningContent, signature included, for replay.
    assert block.provider_raw == {
        "reasoningContent": {"reasoningText": {"text": "let me think", "signature": "abc123"}}
    }


def test_bedrock_replays_thinking_block_verbatim_from_provider_raw():
    """An assistant ``thinking`` block carrying bedrock provider_raw is sent back to
    Converse unchanged — never reserialized from ``text`` (would drop signature)."""
    from cadless.llm.providers.bedrock import _block_to_bedrock

    raw = {"reasoningContent": {"reasoningText": {"text": "t", "signature": "S"}}}
    block = ContentBlock.of_thinking("t", provider="bedrock", provider_raw=raw)
    assert _block_to_bedrock(block) is raw or _block_to_bedrock(block) == raw
    # Identity of structure: signature survives (a reserialize would drop it).
    assert _block_to_bedrock(block)["reasoningContent"]["reasoningText"]["signature"] == "S"


def test_bedrock_capabilities_report_thinking_support():
    provider, _ = _provider_with([])
    caps = provider.capabilities("sonnet-4-6")
    assert caps.supports_thinking is True


def test_bedrock_enables_thinking_request_field_when_params_thinking():
    provider, client = _provider_with(_text_stream("x"))
    list(
        provider.stream_turn(
            model="sonnet-4-6",
            system="s",
            messages=[],
            tools=[],
            params=TurnParams(thinking=True, thinking_budget_tokens=1500),
        )
    )
    body = client.calls[0]
    amrf = body.get("additionalModelRequestFields", {})
    assert amrf["thinking"]["type"] == "enabled"
    assert amrf["thinking"]["budget_tokens"] == 1500


def test_bedrock_forces_temperature_one_when_thinking_enabled():
    """Extended thinking requires ``temperature == 1`` — Bedrock rejects any other
    value (the configured ``bedrock_temperature`` of 0.0 included) with a
    ValidationException. The provider must override it when thinking is on."""
    provider, client = _provider_with(_text_stream("x"))
    list(
        provider.stream_turn(
            model="opus-4-6",
            system="s",
            messages=[],
            tools=[],
            params=TurnParams(thinking=True, temperature=0.0),
        )
    )
    assert client.calls[0]["inferenceConfig"]["temperature"] == 1


def test_bedrock_keeps_configured_temperature_without_thinking():
    """Without thinking, the configured/explicit temperature is respected."""
    provider, client = _provider_with(_text_stream("x"))
    list(
        provider.stream_turn(
            model="sonnet-4-6",
            system="s",
            messages=[],
            tools=[],
            params=TurnParams(temperature=0.0),
        )
    )
    assert client.calls[0]["inferenceConfig"]["temperature"] == 0.0


def test_bedrock_accumulates_tool_input_partial_json():
    events = [
        {"messageStart": {"role": "assistant"}},
        {
            "contentBlockStart": {
                "start": {"toolUse": {"toolUseId": "tu-1", "name": "make_box"}},
                "contentBlockIndex": 0,
            }
        },
        {
            "contentBlockDelta": {
                "delta": {"toolUse": {"input": '{"size":'}},
                "contentBlockIndex": 0,
            }
        },
        {"contentBlockDelta": {"delta": {"toolUse": {"input": "10}"}}, "contentBlockIndex": 0}},
        {"contentBlockStop": {"contentBlockIndex": 0}},
        {"messageStop": {"stopReason": "tool_use"}},
    ]
    provider, _ = _provider_with(events)
    out = list(
        provider.stream_turn(
            model="sonnet-4-6", system="s", messages=[], tools=[], params=TurnParams()
        )
    )
    events_kinds = [c.event for c in out]
    assert StreamEvent.TOOL_USE_START in events_kinds
    assert StreamEvent.TOOL_INPUT_DELTA in events_kinds
    # tool_use_stop carries the accumulated, parsed input + provider_raw block.
    stop = [c for c in out if c.event == StreamEvent.TOOL_USE_STOP][0]
    assert stop.payload["id"] == "tu-1"
    assert stop.payload["name"] == "make_box"
    assert stop.payload["input"] == {"size": 10}
    # provider_raw preserved + tagged provider=bedrock for verbatim replay.
    block: ContentBlock = stop.payload["block"]
    assert block.kind == "tool_use"
    assert block.provider == "bedrock"
    assert block.provider_raw is not None


def test_bedrock_turn_delta_carries_normalized_stop_reason():
    provider, _ = _provider_with(_text_stream("x"))
    out = list(
        provider.stream_turn(
            model="sonnet-4-6", system="s", messages=[], tools=[], params=TurnParams()
        )
    )
    delta = [c for c in out if c.event == StreamEvent.TURN_DELTA][0]
    assert delta.payload["stop_reason"] == StopReason.END_TURN


def test_bedrock_emits_usage_event():
    provider, _ = _provider_with(_text_stream("x"))
    out = list(
        provider.stream_turn(
            model="sonnet-4-6", system="s", messages=[], tools=[], params=TurnParams()
        )
    )
    usage = [c for c in out if c.event == StreamEvent.USAGE]
    assert usage and usage[0].payload["input_tokens"] == 7
    assert usage[0].payload["output_tokens"] == 3


def test_bedrock_complete_concatenates_text():
    provider, _ = _provider_with(_text_stream("from build123d ", "import *"))
    text = provider.complete(model="sonnet-4-6", system="SYS", user="USER")
    assert text == "from build123d import *"


def test_bedrock_complete_sends_system_and_user():
    provider, client = _provider_with(_text_stream("ok"))
    provider.complete(model="sonnet-4-6", system="SYS", user="USER")
    sent = client.calls[0]
    assert sent["system"] == [{"text": "SYS"}]
    assert sent["messages"][0]["role"] == "user"


def test_bedrock_passes_tools_when_provided():
    provider, client = _provider_with(_text_stream("x"))
    tool = ToolDef(
        name="make_box", description="make a box", input_schema={"type": "object", "properties": {}}
    )
    list(
        provider.stream_turn(
            model="sonnet-4-6", system="s", messages=[], tools=[tool], params=TurnParams()
        )
    )
    sent = client.calls[0]
    specs = sent["toolConfig"]["tools"]
    assert specs[0]["toolSpec"]["name"] == "make_box"


def test_bedrock_capabilities_reports_thinking_and_tool_choice():
    provider, _ = _provider_with([])
    caps: Capabilities = provider.capabilities("sonnet-4-6")
    assert caps.supports_thinking is True
    assert caps.supports_tool_choice is True
    assert caps.max_output_tokens > 0


def test_bedrock_satisfies_chatprovider_protocol():
    provider, _ = _provider_with([])
    assert isinstance(provider, ChatProvider)


@pytest.mark.bedrock
def test_live_bedrock_stream_smoke():
    os.environ.setdefault("AWS_REGION", "us-east-1")
    provider = build_provider("bedrock", settings=Settings())
    text = provider.complete(
        model="sonnet-4-6",
        system="You are a calculator. Reply with digits only.",
        user="2+2=",
    )
    assert "4" in text


# --- embed() seam -----------------------------------------------


def test_embed_is_on_chatprovider_protocol():
    # The vendor-neutral seam exposes embed() alongside stream_turn/complete.
    assert hasattr(ChatProvider, "embed")


def test_fake_embed_single_text_returns_flat_vector():
    provider = FakeChatProvider()
    vec = provider.embed("hello world")
    assert isinstance(vec, list)
    assert all(isinstance(x, float) for x in vec)
    assert len(vec) == Settings().embed_dimensions


def test_fake_embed_batch_returns_list_of_vectors():
    provider = FakeChatProvider()
    vecs = provider.embed(["a", "b", "c"])
    assert isinstance(vecs, list)
    assert len(vecs) == 3
    assert all(len(v) == Settings().embed_dimensions for v in vecs)
    assert all(isinstance(x, float) for v in vecs for x in v)


def test_fake_embed_is_deterministic_for_same_text():
    provider = FakeChatProvider()
    assert provider.embed("plate with hole") == provider.embed("plate with hole")
    # Distinct text -> distinct vector.
    assert provider.embed("foo") != provider.embed("bar")


def test_fake_embed_satisfies_protocol_via_isinstance():
    provider: ChatProvider = FakeChatProvider()
    assert isinstance(provider, ChatProvider)


def test_embed_reachable_via_registry():
    provider = build_provider("fake", settings=Settings())
    vec = provider.embed("via registry")
    assert len(vec) == Settings().embed_dimensions


def test_bedrock_provider_exposes_embed():
    provider, _ = _provider_with([])
    assert hasattr(provider, "embed")


class _FakeInvokeClient:
    """Stand-in for boto3 bedrock-runtime; captures invoke_model calls."""

    def __init__(self, embedding):
        self._embedding = embedding
        self.calls: list[dict] = []

    def invoke_model(self, **kwargs):
        self.calls.append(kwargs)
        import io
        import json as _json

        payload = _json.dumps({"embedding": self._embedding}).encode()
        return {"body": io.BytesIO(payload)}


def test_bedrock_embed_single_sends_titan_request_shape():
    embedding = [0.1, 0.2, 0.3]
    client = _FakeInvokeClient(embedding)
    provider = BedrockChatProvider(config=Settings(), client=client)
    vec = provider.embed("a plate with a hole")
    assert vec == embedding
    sent = client.calls[0]
    assert sent["modelId"] == "amazon.titan-embed-text-v2:0"
    import json as _json

    body = _json.loads(sent["body"])
    assert body["inputText"] == "a plate with a hole"
    assert body["dimensions"] == Settings().embed_dimensions


def test_bedrock_embed_batch_invokes_per_text():
    client = _FakeInvokeClient([1.0, 2.0])
    provider = BedrockChatProvider(config=Settings(), client=client)
    vecs = provider.embed(["one", "two"])
    assert vecs == [[1.0, 2.0], [1.0, 2.0]]
    assert len(client.calls) == 2
    import json as _json

    assert _json.loads(client.calls[0]["body"])["inputText"] == "one"
    assert _json.loads(client.calls[1]["body"])["inputText"] == "two"


@pytest.mark.bedrock
def test_live_bedrock_embed_smoke():
    os.environ.setdefault("AWS_REGION", "us-east-1")
    provider = build_provider("bedrock", settings=Settings())
    vec = provider.embed("a 50mm cube")
    assert isinstance(vec, list)
    assert len(vec) == Settings().embed_dimensions
    assert all(isinstance(x, float) for x in vec)


# --- anthropic adapter (synthetic stream, no network) -----------------------


class _FakeAnthropicMessages:
    """The ``client.messages`` namespace of the fake Anthropic client."""

    def __init__(self, owner):
        self._owner = owner

    def create(self, **kwargs):
        self._owner.calls.append(kwargs)
        return iter(self._owner.events)


class _FakeAnthropicClient:
    """Stand-in for anthropic.Anthropic; replays a scripted Messages stream."""

    def __init__(self, events):
        self.events = events
        self.calls: list[dict] = []
        self.messages = _FakeAnthropicMessages(self)


def _anthropic_text_stream(*texts):
    """A minimal Messages API event stream emitting one text content block."""
    evs = [
        {"type": "message_start", "message": {"usage": {"input_tokens": 7, "output_tokens": 1}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
    ]
    for t in texts:
        evs.append(
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": t}}
        )
    evs += [
        {"type": "content_block_stop", "index": 0},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {"output_tokens": 3},
        },
        {"type": "message_stop"},
    ]
    return evs


def _anthropic_with(events):
    client = _FakeAnthropicClient(events)
    return AnthropicChatProvider(config=Settings(), client=client), client


def test_anthropic_maps_text_stream_to_neutral_events():
    provider, _ = _anthropic_with(_anthropic_text_stream("hi ", "there"))
    out = list(
        provider.stream_turn(
            model="sonnet-4-6",
            system="s",
            messages=[Message(role="user", content=[ContentBlock.of_text("q")])],
            tools=[],
            params=TurnParams(),
        )
    )
    events = [c.event for c in out]
    assert events[0] == StreamEvent.TURN_START
    assert StreamEvent.TEXT_DELTA in events
    assert StreamEvent.TURN_DELTA in events
    assert events[-1] == StreamEvent.TURN_STOP
    text = "".join(c.payload["text"] for c in out if c.event == StreamEvent.TEXT_DELTA)
    assert text == "hi there"


def test_anthropic_resolves_slug_to_api_model_and_streams():
    provider, client = _anthropic_with(_anthropic_text_stream("x"))
    list(
        provider.stream_turn(
            model="sonnet-4-6", system="s", messages=[], tools=[], params=TurnParams()
        )
    )
    assert client.calls[0]["model"] == "claude-sonnet-4-6"
    assert client.calls[0]["stream"] is True


def test_anthropic_passes_raw_claude_model_id_through():
    provider, client = _anthropic_with(_anthropic_text_stream("x"))
    list(
        provider.stream_turn(
            model="claude-sonnet-4-5-20250929",
            system="s",
            messages=[],
            tools=[],
            params=TurnParams(),
        )
    )
    assert client.calls[0]["model"] == "claude-sonnet-4-5-20250929"


def test_anthropic_unknown_slug_fails_fast():
    provider, _ = _anthropic_with(_anthropic_text_stream("x"))
    with pytest.raises(KeyError):
        list(
            provider.stream_turn(
                model="gpt-4o", system="s", messages=[], tools=[], params=TurnParams()
            )
        )


def test_anthropic_accumulates_thinking_into_block_with_signature():
    """Thinking deltas stream live; the stop carries a neutral ``thinking`` block
    whose ``provider_raw`` is the verbatim Messages block (signature included)."""
    events = [
        {"type": "message_start", "message": {"usage": {"input_tokens": 1}}},
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "thinking", "thinking": "", "signature": ""},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "thinking_delta", "thinking": "let me "},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "thinking_delta", "thinking": "think"},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "signature_delta", "signature": "abc123"},
        },
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}},
        {"type": "message_stop"},
    ]
    provider, _ = _anthropic_with(events)
    out = list(
        provider.stream_turn(
            model="sonnet-4-6", system="s", messages=[], tools=[], params=TurnParams()
        )
    )
    deltas = [c for c in out if c.event == StreamEvent.THINKING_DELTA]
    assert "".join(c.payload["text"] for c in deltas) == "let me think"
    stops = [c for c in out if c.event == StreamEvent.THINKING_STOP]
    assert len(stops) == 1
    block: ContentBlock = stops[0].payload["block"]
    assert block.kind == "thinking"
    assert block.text == "let me think"
    assert block.provider == "anthropic"
    assert block.provider_raw == {
        "type": "thinking",
        "thinking": "let me think",
        "signature": "abc123",
    }


def test_anthropic_enables_thinking_and_forces_temperature_one():
    provider, client = _anthropic_with(_anthropic_text_stream("x"))
    list(
        provider.stream_turn(
            model="opus-4-6",
            system="s",
            messages=[],
            tools=[],
            params=TurnParams(thinking=True, thinking_budget_tokens=1500, temperature=0.0),
        )
    )
    sent = client.calls[0]
    assert sent["thinking"] == {"type": "enabled", "budget_tokens": 1500}
    assert sent["temperature"] == 1


def test_anthropic_keeps_configured_temperature_without_thinking():
    provider, client = _anthropic_with(_anthropic_text_stream("x"))
    list(
        provider.stream_turn(
            model="sonnet-4-6", system="s", messages=[], tools=[], params=TurnParams()
        )
    )
    assert client.calls[0]["temperature"] == 0.0


def test_anthropic_accumulates_tool_input_partial_json():
    events = [
        {"type": "message_start", "message": {"usage": {"input_tokens": 2}}},
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "tool_use", "id": "tu-1", "name": "make_box", "input": {}},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": '{"size":'},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": "10}"},
        },
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use"}},
        {"type": "message_stop"},
    ]
    provider, _ = _anthropic_with(events)
    out = list(
        provider.stream_turn(
            model="sonnet-4-6", system="s", messages=[], tools=[], params=TurnParams()
        )
    )
    kinds = [c.event for c in out]
    assert StreamEvent.TOOL_USE_START in kinds
    assert StreamEvent.TOOL_INPUT_DELTA in kinds
    stop = [c for c in out if c.event == StreamEvent.TOOL_USE_STOP][0]
    assert stop.payload["id"] == "tu-1"
    assert stop.payload["name"] == "make_box"
    assert stop.payload["input"] == {"size": 10}
    block: ContentBlock = stop.payload["block"]
    assert block.kind == "tool_use"
    assert block.provider == "anthropic"
    assert block.provider_raw == {
        "type": "tool_use",
        "id": "tu-1",
        "name": "make_box",
        "input": {"size": 10},
    }
    delta = [c for c in out if c.event == StreamEvent.TURN_DELTA][0]
    assert delta.payload["stop_reason"] == StopReason.TOOL_USE


def test_anthropic_turn_delta_normalizes_stop_reasons():
    provider, _ = _anthropic_with(_anthropic_text_stream("x"))
    out = list(
        provider.stream_turn(
            model="sonnet-4-6", system="s", messages=[], tools=[], params=TurnParams()
        )
    )
    delta = [c for c in out if c.event == StreamEvent.TURN_DELTA][0]
    assert delta.payload["stop_reason"] == StopReason.END_TURN

    events = _anthropic_text_stream("x")
    events[-2] = {
        "type": "message_delta",
        "delta": {"stop_reason": "max_tokens"},
        "usage": {"output_tokens": 3},
    }
    provider, _ = _anthropic_with(events)
    out = list(
        provider.stream_turn(
            model="sonnet-4-6", system="s", messages=[], tools=[], params=TurnParams()
        )
    )
    delta = [c for c in out if c.event == StreamEvent.TURN_DELTA][0]
    assert delta.payload["stop_reason"] == StopReason.MAX_TOKENS


def test_anthropic_emits_usage_from_start_and_delta():
    provider, _ = _anthropic_with(_anthropic_text_stream("x"))
    out = list(
        provider.stream_turn(
            model="sonnet-4-6", system="s", messages=[], tools=[], params=TurnParams()
        )
    )
    usage = [c for c in out if c.event == StreamEvent.USAGE]
    assert usage and usage[0].payload["input_tokens"] == 7
    assert usage[0].payload["output_tokens"] == 3


def test_anthropic_ignores_ping_events():
    events = _anthropic_text_stream("x")
    events.insert(1, {"type": "ping"})
    provider, _ = _anthropic_with(events)
    out = list(
        provider.stream_turn(
            model="sonnet-4-6", system="s", messages=[], tools=[], params=TurnParams()
        )
    )
    assert out[-1].event == StreamEvent.TURN_STOP


def test_anthropic_raises_on_stream_error_event():
    events = [
        {"type": "message_start", "message": {"usage": {"input_tokens": 1}}},
        {"type": "error", "error": {"type": "overloaded_error", "message": "busy"}},
    ]
    provider, _ = _anthropic_with(events)
    with pytest.raises(RuntimeError, match="overloaded_error"):
        list(
            provider.stream_turn(
                model="sonnet-4-6", system="s", messages=[], tools=[], params=TurnParams()
            )
        )


def test_anthropic_complete_concatenates_text_and_sends_shapes():
    provider, client = _anthropic_with(_anthropic_text_stream("from build123d ", "import *"))
    text = provider.complete(model="sonnet-4-6", system="SYS", user="USER")
    assert text == "from build123d import *"
    sent = client.calls[0]
    assert sent["system"] == "SYS"
    assert sent["messages"][0]["role"] == "user"
    assert sent["messages"][0]["content"] == [{"type": "text", "text": "USER"}]
    assert sent["max_tokens"] == Settings().bedrock_max_tokens


def test_anthropic_passes_tools_and_maps_tool_choice():
    provider, client = _anthropic_with(_anthropic_text_stream("x"))
    tool = ToolDef(
        name="make_box", description="make a box", input_schema={"type": "object", "properties": {}}
    )
    list(
        provider.stream_turn(
            model="sonnet-4-6",
            system="s",
            messages=[],
            tools=[tool],
            params=TurnParams(tool_choice="any"),
        )
    )
    sent = client.calls[0]
    assert sent["tools"] == [
        {
            "name": "make_box",
            "description": "make a box",
            "input_schema": {"type": "object", "properties": {}},
        }
    ]
    assert sent["tool_choice"] == {"type": "any"}

    provider, client = _anthropic_with(_anthropic_text_stream("x"))
    list(
        provider.stream_turn(
            model="sonnet-4-6",
            system="s",
            messages=[],
            tools=[tool],
            params=TurnParams(tool_choice="auto"),
        )
    )
    assert client.calls[0]["tool_choice"] == {"type": "auto"}


def test_anthropic_replays_thinking_block_verbatim_from_provider_raw():
    from cadless.llm.providers.anthropic import _block_to_anthropic

    raw = {"type": "thinking", "thinking": "t", "signature": "S"}
    block = ContentBlock.of_thinking("t", provider="anthropic", provider_raw=raw)
    assert _block_to_anthropic(block) == raw
    assert _block_to_anthropic(block)["signature"] == "S"


def test_anthropic_drops_foreign_thinking_blocks():
    """Thinking from another vendor cannot be replayed to the Messages API (no
    valid signature) — it is dropped; a message left empty by the drop is omitted."""
    provider, client = _anthropic_with(_anthropic_text_stream("x"))
    bedrock_raw = {"reasoningContent": {"reasoningText": {"text": "t", "signature": "S"}}}
    messages = [
        Message(role="user", content=[ContentBlock.of_text("q")]),
        Message(
            role="assistant",
            content=[
                ContentBlock.of_thinking("t", provider="bedrock", provider_raw=bedrock_raw),
                ContentBlock.of_text("I'll build"),
            ],
        ),
        Message(
            role="assistant",
            content=[
                ContentBlock.of_thinking(
                    "only thinking", provider="bedrock", provider_raw=bedrock_raw
                ),
            ],
        ),
    ]
    list(
        provider.stream_turn(
            model="sonnet-4-6", system="s", messages=messages, tools=[], params=TurnParams()
        )
    )
    sent = client.calls[0]["messages"]
    assert len(sent) == 2
    assert sent[1] == {"role": "assistant", "content": [{"type": "text", "text": "I'll build"}]}


def test_anthropic_encodes_tool_use_and_tool_result_blocks():
    provider, client = _anthropic_with(_anthropic_text_stream("x"))
    messages = [
        Message(
            role="assistant",
            content=[ContentBlock.of_tool_use(id="tu-1", name="make_box", input={"size": 10})],
        ),
        Message(
            role="user",
            content=[
                ContentBlock.of_tool_result(tool_use_id="tu-1", content="boom", is_error=True)
            ],
        ),
    ]
    list(
        provider.stream_turn(
            model="sonnet-4-6", system="s", messages=messages, tools=[], params=TurnParams()
        )
    )
    sent = client.calls[0]["messages"]
    assert sent[0]["content"] == [
        {"type": "tool_use", "id": "tu-1", "name": "make_box", "input": {"size": 10}}
    ]
    assert sent[1]["content"] == [
        {"type": "tool_result", "tool_use_id": "tu-1", "content": "boom", "is_error": True}
    ]


def test_anthropic_embed_raises_typed_embeddings_unsupported():
    # No injected client: embed must refuse BEFORE any SDK/client work.
    provider = AnthropicChatProvider(config=Settings())
    with pytest.raises(EmbeddingsUnsupported):
        provider.embed("a plate")
    with pytest.raises(EmbeddingsUnsupported):
        provider.embed(["a", "b"])


def test_anthropic_capabilities_and_protocol():
    provider, _ = _anthropic_with([])
    caps: Capabilities = provider.capabilities("sonnet-4-6")
    assert caps.supports_thinking is True
    assert caps.supports_tool_choice is True
    assert caps.max_output_tokens > 0
    assert isinstance(provider, ChatProvider)


def test_registry_builds_anthropic():
    assert "anthropic" in available_providers()
    provider = build_provider("anthropic", settings=Settings())
    assert type(provider).__name__ == "AnthropicChatProvider"
    assert isinstance(provider, ChatProvider)


@pytest.mark.anthropic
def test_live_anthropic_stream_smoke():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set")
    provider = build_provider("anthropic", settings=Settings())
    text = provider.complete(
        model="haiku-4-5",
        system="You are a calculator. Reply with digits only.",
        user="2+2=",
    )
    assert "4" in text


# --- openai adapter (synthetic stream, no network) ---------------------------


class _FakeOpenAIChatCompletions:
    def __init__(self, owner):
        self._owner = owner

    def create(self, **kwargs):
        self._owner.calls.append(kwargs)
        return iter(self._owner.chunks)


class _FakeOpenAIChat:
    def __init__(self, owner):
        self.completions = _FakeOpenAIChatCompletions(owner)


class _FakeOpenAIEmbeddings:
    def __init__(self, owner):
        self._owner = owner

    def create(self, **kwargs):
        self._owner.embed_calls.append(kwargs)
        return self._owner.embed_response


class _FakeOpenAIClient:
    """Stand-in for openai.OpenAI; replays scripted chat chunks + embeddings."""

    def __init__(self, chunks=(), embed_response=None):
        self.chunks = list(chunks)
        self.embed_response = embed_response
        self.calls: list[dict] = []
        self.embed_calls: list[dict] = []
        self.chat = _FakeOpenAIChat(self)
        self.embeddings = _FakeOpenAIEmbeddings(self)


def _openai_text_stream(*texts):
    """A minimal Chat Completions chunk stream emitting text, finish, usage."""
    chunks = [
        {
            "choices": [
                {"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}
            ]
        }
    ]
    for t in texts:
        chunks.append({"choices": [{"index": 0, "delta": {"content": t}, "finish_reason": None}]})
    chunks += [
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        {"choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": 3}},
    ]
    return chunks


def _openai_with(chunks, embed_response=None):
    client = _FakeOpenAIClient(chunks, embed_response)
    return OpenAIChatProvider(config=Settings(), client=client), client


def test_openai_maps_text_stream_to_neutral_events():
    provider, _ = _openai_with(_openai_text_stream("hi ", "there"))
    out = list(
        provider.stream_turn(
            model="gpt-4o",
            system="s",
            messages=[Message(role="user", content=[ContentBlock.of_text("q")])],
            tools=[],
            params=TurnParams(),
        )
    )
    events = [c.event for c in out]
    assert events[0] == StreamEvent.TURN_START
    assert StreamEvent.TEXT_DELTA in events
    assert StreamEvent.TURN_DELTA in events
    assert events[-1] == StreamEvent.TURN_STOP
    text = "".join(c.payload["text"] for c in out if c.event == StreamEvent.TEXT_DELTA)
    assert text == "hi there"
    delta = [c for c in out if c.event == StreamEvent.TURN_DELTA][0]
    assert delta.payload["stop_reason"] == StopReason.END_TURN
    usage = [c for c in out if c.event == StreamEvent.USAGE]
    assert usage and usage[0].payload == {"input_tokens": 7, "output_tokens": 3}


def test_openai_request_streams_with_usage_and_output_cap():
    provider, client = _openai_with(_openai_text_stream("x"))
    list(
        provider.stream_turn(model="gpt-4o", system="s", messages=[], tools=[], params=TurnParams())
    )
    sent = client.calls[0]
    assert sent["model"] == "gpt-4o"
    assert sent["stream"] is True
    assert sent["stream_options"] == {"include_usage": True}
    assert sent["max_completion_tokens"] == Settings().bedrock_max_tokens
    assert sent["temperature"] == 0.0


def test_openai_rejects_bedrock_slugs_with_guidance():
    provider, _ = _openai_with(_openai_text_stream("x"))
    with pytest.raises(ValueError, match="CADLESS_ORCHESTRATOR_MODEL"):
        list(
            provider.stream_turn(
                model="sonnet-4-6", system="s", messages=[], tools=[], params=TurnParams()
            )
        )


def test_openai_translates_message_history():
    import json as _json

    provider, client = _openai_with(_openai_text_stream("x"))
    messages = [
        Message(role="user", content=[ContentBlock.of_text("make a box")]),
        Message(
            role="assistant",
            content=[
                ContentBlock.of_thinking(
                    "hmm", provider="anthropic", provider_raw={"type": "thinking"}
                ),
                ContentBlock.of_text("building"),
                ContentBlock.of_tool_use(id="call_1", name="make_box", input={"size": 10}),
            ],
        ),
        Message(
            role="user",
            content=[
                ContentBlock.of_tool_result(tool_use_id="call_1", content="ok"),
                ContentBlock.of_text("looks good"),
            ],
        ),
    ]
    list(
        provider.stream_turn(
            model="gpt-4o", system="SYS", messages=messages, tools=[], params=TurnParams()
        )
    )
    sent = client.calls[0]["messages"]
    assert sent[0] == {"role": "system", "content": "SYS"}
    assert sent[1] == {"role": "user", "content": "make a box"}
    assert sent[2]["role"] == "assistant"
    assert sent[2]["content"] == "building"
    [tc] = sent[2]["tool_calls"]
    assert tc["id"] == "call_1"
    assert tc["type"] == "function"
    assert tc["function"]["name"] == "make_box"
    assert _json.loads(tc["function"]["arguments"]) == {"size": 10}
    assert sent[3] == {"role": "tool", "tool_call_id": "call_1", "content": "ok"}
    assert sent[4] == {"role": "user", "content": "looks good"}
    # the foreign thinking block was dropped, not re-encoded
    assert all("thinking" not in str(m) for m in sent)


def test_openai_marks_tool_result_errors_in_content():
    provider, client = _openai_with(_openai_text_stream("x"))
    messages = [
        Message(
            role="user",
            content=[
                ContentBlock.of_tool_result(tool_use_id="call_1", content="boom", is_error=True)
            ],
        )
    ]
    list(
        provider.stream_turn(
            model="gpt-4o", system="s", messages=messages, tools=[], params=TurnParams()
        )
    )
    sent = client.calls[0]["messages"]
    assert sent[1] == {"role": "tool", "tool_call_id": "call_1", "content": "[tool error] boom"}


def test_openai_accumulates_indexed_tool_call_fragments():
    chunks = [
        {"choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]},
        {
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_1",
                                "type": "function",
                                "function": {"name": "make_box", "arguments": ""},
                            }
                        ]
                    },
                    "finish_reason": None,
                }
            ]
        },
        {
            "choices": [
                {
                    "index": 0,
                    "delta": {"tool_calls": [{"index": 0, "function": {"arguments": '{"size":'}}]},
                    "finish_reason": None,
                }
            ]
        },
        {
            "choices": [
                {
                    "index": 0,
                    "delta": {"tool_calls": [{"index": 0, "function": {"arguments": "10}"}}]},
                    "finish_reason": None,
                }
            ]
        },
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
    ]
    provider, _ = _openai_with(chunks)
    out = list(
        provider.stream_turn(model="gpt-4o", system="s", messages=[], tools=[], params=TurnParams())
    )
    kinds = [c.event for c in out]
    assert kinds[0] == StreamEvent.TURN_START
    start = [c for c in out if c.event == StreamEvent.TOOL_USE_START][0]
    assert start.payload == {"id": "call_1", "name": "make_box"}
    deltas = [c for c in out if c.event == StreamEvent.TOOL_INPUT_DELTA]
    assert [c.payload["partial_json"] for c in deltas] == ['{"size":', "10}"]
    stop = [c for c in out if c.event == StreamEvent.TOOL_USE_STOP][0]
    assert stop.payload["id"] == "call_1"
    assert stop.payload["name"] == "make_box"
    assert stop.payload["input"] == {"size": 10}
    block: ContentBlock = stop.payload["block"]
    assert block.kind == "tool_use"
    assert block.provider == "openai"
    assert block.provider_raw == {
        "id": "call_1",
        "type": "function",
        "function": {"name": "make_box", "arguments": '{"size":10}'},
    }
    # the pending call closes BEFORE the turn-level stop reason is reported
    assert kinds.index(StreamEvent.TOOL_USE_STOP) < kinds.index(StreamEvent.TURN_DELTA)
    delta = [c for c in out if c.event == StreamEvent.TURN_DELTA][0]
    assert delta.payload["stop_reason"] == StopReason.TOOL_USE


def test_openai_closes_tool_calls_sequentially():
    chunks = [
        {
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_1",
                                "type": "function",
                                "function": {"name": "make_box", "arguments": '{"a":1}'},
                            }
                        ]
                    },
                    "finish_reason": None,
                }
            ]
        },
        {
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 1,
                                "id": "call_2",
                                "type": "function",
                                "function": {"name": "make_lid", "arguments": '{"b":2}'},
                            }
                        ]
                    },
                    "finish_reason": None,
                }
            ]
        },
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
    ]
    provider, _ = _openai_with(chunks)
    out = list(
        provider.stream_turn(model="gpt-4o", system="s", messages=[], tools=[], params=TurnParams())
    )
    events = [
        (c.event, c.payload.get("id"))
        for c in out
        if c.event in (StreamEvent.TOOL_USE_START, StreamEvent.TOOL_USE_STOP)
    ]
    assert events == [
        (StreamEvent.TOOL_USE_START, "call_1"),
        (StreamEvent.TOOL_USE_STOP, "call_1"),
        (StreamEvent.TOOL_USE_START, "call_2"),
        (StreamEvent.TOOL_USE_STOP, "call_2"),
    ]
    stops = [c for c in out if c.event == StreamEvent.TOOL_USE_STOP]
    assert stops[0].payload["input"] == {"a": 1}
    assert stops[1].payload["input"] == {"b": 2}


def test_openai_maps_finish_reasons():
    chunks = _openai_text_stream("x")
    chunks[-2] = {"choices": [{"index": 0, "delta": {}, "finish_reason": "length"}]}
    provider, _ = _openai_with(chunks)
    out = list(
        provider.stream_turn(model="gpt-4o", system="s", messages=[], tools=[], params=TurnParams())
    )
    delta = [c for c in out if c.event == StreamEvent.TURN_DELTA][0]
    assert delta.payload["stop_reason"] == StopReason.MAX_TOKENS


def test_openai_omits_sampling_params_for_reasoning_models():
    provider, client = _openai_with(_openai_text_stream("x"))
    list(
        provider.stream_turn(
            model="o3-mini",
            system="s",
            messages=[],
            tools=[],
            params=TurnParams(stop_sequences=["END"]),
        )
    )
    sent = client.calls[0]
    assert "temperature" not in sent
    assert "stop" not in sent
    assert sent["max_completion_tokens"] == Settings().bedrock_max_tokens

    provider, client = _openai_with(_openai_text_stream("x"))
    list(
        provider.stream_turn(
            model="gpt-5-mini", system="s", messages=[], tools=[], params=TurnParams()
        )
    )
    assert "temperature" not in client.calls[0]


def test_openai_passes_tools_tool_choice_and_stop():
    provider, client = _openai_with(_openai_text_stream("x"))
    tool = ToolDef(
        name="make_box", description="make a box", input_schema={"type": "object", "properties": {}}
    )
    list(
        provider.stream_turn(
            model="gpt-4o",
            system="s",
            messages=[],
            tools=[tool],
            params=TurnParams(tool_choice="any", stop_sequences=["END"]),
        )
    )
    sent = client.calls[0]
    assert sent["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "make_box",
                "description": "make a box",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    assert sent["tool_choice"] == "required"
    assert sent["stop"] == ["END"]

    provider, client = _openai_with(_openai_text_stream("x"))
    list(
        provider.stream_turn(
            model="gpt-4o",
            system="s",
            messages=[],
            tools=[tool],
            params=TurnParams(tool_choice="auto"),
        )
    )
    assert client.calls[0]["tool_choice"] == "auto"


def test_openai_replays_tool_call_provider_raw_verbatim():
    provider, client = _openai_with(_openai_text_stream("x"))
    raw = {
        "id": "call_9",
        "type": "function",
        "function": {"name": "make_box", "arguments": '{"size":10}'},
    }
    messages = [
        Message(
            role="assistant",
            content=[
                ContentBlock.of_tool_use(
                    id="call_9",
                    name="make_box",
                    input={"size": 10},
                    provider="openai",
                    provider_raw=raw,
                )
            ],
        )
    ]
    list(
        provider.stream_turn(
            model="gpt-4o", system="s", messages=messages, tools=[], params=TurnParams()
        )
    )
    assert client.calls[0]["messages"][1]["tool_calls"] == [raw]


def test_openai_embed_single_and_batch_align_by_index():
    resp = {"data": [{"index": 1, "embedding": [3.0, 4.0]}, {"index": 0, "embedding": [1.0, 2.0]}]}
    provider, client = _openai_with([], embed_response=resp)
    vecs = provider.embed(["one", "two"])
    assert vecs == [[1.0, 2.0], [3.0, 4.0]]
    sent = client.embed_calls[0]
    assert sent["model"] == "text-embedding-3-small"
    assert sent["input"] == ["one", "two"]
    assert sent["dimensions"] == Settings().embed_dimensions

    single = {"data": [{"index": 0, "embedding": [0.5, 0.25]}]}
    provider, client = _openai_with([], embed_response=single)
    vec = provider.embed("a plate")
    assert vec == [0.5, 0.25]
    assert client.embed_calls[0]["input"] == ["a plate"]


def test_openai_embed_omits_dimensions_for_non_v3_models():
    resp = {"data": [{"index": 0, "embedding": [1.0]}]}
    cfg = Settings(openai_embed_model="text-embedding-ada-002")
    client = _FakeOpenAIClient([], resp)
    provider = OpenAIChatProvider(config=cfg, client=client)
    provider.embed("x")
    assert "dimensions" not in client.embed_calls[0]


def test_openai_complete_concatenates_text():
    provider, _ = _openai_with(_openai_text_stream("from build123d ", "import *"))
    text = provider.complete(model="gpt-4o", system="SYS", user="USER")
    assert text == "from build123d import *"


def test_openai_capabilities_and_protocol():
    provider, _ = _openai_with([])
    caps: Capabilities = provider.capabilities("gpt-4o")
    assert caps.supports_thinking is False
    assert caps.supports_tool_choice is True
    assert caps.max_output_tokens > 0
    assert isinstance(provider, ChatProvider)


def test_registry_builds_openai():
    assert "openai" in available_providers()
    provider = build_provider("openai", settings=Settings())
    assert type(provider).__name__ == "OpenAIChatProvider"
    assert isinstance(provider, ChatProvider)


def test_settings_declare_openai_embed_model():
    assert Settings().openai_embed_model == "text-embedding-3-small"


@pytest.mark.openai
def test_live_openai_stream_smoke():
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY not set")
    provider = build_provider("openai", settings=Settings())
    text = provider.complete(
        model="gpt-4o-mini",
        system="You are a calculator. Reply with digits only.",
        user="2+2=",
    )
    assert "4" in text


@pytest.mark.openai
def test_live_openai_embed_smoke():
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY not set")
    provider = build_provider("openai", settings=Settings())
    vec = provider.embed("a 50mm cube")
    assert isinstance(vec, list)
    assert len(vec) == Settings().embed_dimensions
    assert all(isinstance(x, float) for x in vec)
