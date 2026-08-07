"""Provider-neutral LLM domain + registry tests.

The types are vendor-free; the registry resolves a provider by name. A tiny
in-test stub provider stands in for the real fake/bedrock providers (those land
in later issues).
"""

from __future__ import annotations

import inspect
from collections.abc import Iterator

import pytest

from cadless.config import Settings
from cadless.llm import registry as registry_mod
from cadless.llm.provider import ChatProvider
from cadless.llm.registry import build_provider
from cadless.llm.types import (
    Capabilities,
    ContentBlock,
    Message,
    StopReason,
    StreamEvent,
    ToolDef,
    TurnParams,
    Usage,
)

# --- neutral types ---------------------------------------------------------


def test_content_block_kinds_construct():
    text = ContentBlock.of_text("hello")
    thinking = ContentBlock.of_thinking("let me think")
    tool_use = ContentBlock.of_tool_use(id="t1", name="make_box", input={"x": 1})
    tool_result = ContentBlock.of_tool_result(tool_use_id="t1", content="ok")
    assert text.kind == "text"
    assert text.text == "hello"
    assert thinking.kind == "thinking"
    assert tool_use.kind == "tool_use"
    assert tool_use.name == "make_box"
    assert tool_use.input == {"x": 1}
    assert tool_result.kind == "tool_result"
    assert tool_result.tool_use_id == "t1"


def test_content_block_carries_provider_tag_and_raw():
    block = ContentBlock.of_text(
        "hi", provider="bedrock", provider_raw={"type": "text", "text": "hi"}
    )
    assert block.provider == "bedrock"
    assert block.provider_raw == {"type": "text", "text": "hi"}


def test_content_block_provider_defaults_none():
    block = ContentBlock.of_text("hi")
    assert block.provider is None
    assert block.provider_raw is None


def test_message_holds_role_and_blocks():
    msg = Message(role="user", content=[ContentBlock.of_text("hello")])
    assert msg.role == "user"
    assert msg.content[0].text == "hello"


def test_message_serializes_round_trip():
    msg = Message(
        role="assistant",
        content=[
            ContentBlock.of_text("done"),
            ContentBlock.of_tool_use(id="t1", name="box", input={"x": 1}),
        ],
    )
    data = msg.model_dump()
    restored = Message.model_validate(data)
    assert restored == msg


def test_tool_def_construct():
    td = ToolDef(
        name="make_box",
        description="make a box",
        input_schema={"type": "object", "properties": {"x": {"type": "number"}}},
    )
    assert td.name == "make_box"
    assert td.input_schema["type"] == "object"


def test_stream_event_enum_has_required_members():
    expected = {
        "turn_start",
        "text_delta",
        "thinking_delta",
        "tool_use_start",
        "tool_input_delta",
        "tool_use_stop",
        "turn_delta",
        "usage",
        "turn_stop",
    }
    members = {e.value for e in StreamEvent}
    assert expected <= members


def test_stop_reason_enum_members():
    assert StopReason.END_TURN.value == "end_turn"
    assert StopReason.TOOL_USE.value == "tool_use"
    assert StopReason.MAX_TOKENS.value == "max_tokens"


def test_usage_construct_and_defaults():
    u = Usage(input_tokens=10, output_tokens=5)
    assert u.input_tokens == 10
    assert u.output_tokens == 5


def test_capabilities_reports_thinking_toolchoice_maxtokens():
    cap = Capabilities(
        supports_thinking=True,
        supports_tool_choice=True,
        max_output_tokens=8192,
    )
    assert cap.supports_thinking is True
    assert cap.supports_tool_choice is True
    assert cap.max_output_tokens == 8192


def test_turn_params_defaults_and_override():
    p = TurnParams()
    assert p.max_tokens is None or isinstance(p.max_tokens, int)
    p2 = TurnParams(max_tokens=1234, temperature=0.3, thinking=True)
    assert p2.max_tokens == 1234
    assert p2.temperature == 0.3
    assert p2.thinking is True


# --- vendor-free guarantee -------------------------------------------------


def test_types_and_provider_are_vendor_free():
    import cadless.llm.provider as provider_mod
    import cadless.llm.types as types_mod

    for mod in (types_mod, provider_mod):
        src = inspect.getsource(mod)
        assert "import boto3" not in src
        assert "import anthropic" not in src
        assert "from anthropic" not in src
        assert "from boto3" not in src


# --- ChatProvider protocol + registry --------------------------------------


class _StubProvider:
    """Tiny in-test provider implementing the ChatProvider protocol."""

    def stream_turn(self, *, model, system, messages, tools, params) -> Iterator[StreamEvent]:
        yield StreamEvent.TURN_START
        yield StreamEvent.TURN_STOP

    def capabilities(self, model: str) -> Capabilities:
        return Capabilities(
            supports_thinking=False, supports_tool_choice=False, max_output_tokens=4096
        )

    def complete(self, *, model: str, system: str, user: str) -> str:
        return "stub"

    def embed(self, text):
        if isinstance(text, str):
            return [0.0]
        return [[0.0] for _ in text]


def test_stub_satisfies_chatprovider_protocol():
    provider: ChatProvider = _StubProvider()
    assert isinstance(provider, ChatProvider)
    assert provider.complete(model="m", system="s", user="u") == "stub"
    assert provider.capabilities("m").max_output_tokens == 4096
    events = list(
        provider.stream_turn(model="m", system="s", messages=[], tools=[], params=TurnParams())
    )
    assert events[0] is StreamEvent.TURN_START
    assert events[-1] is StreamEvent.TURN_STOP


def test_build_provider_resolves_registered_name():
    registry_mod.register_provider("stub", lambda settings: _StubProvider())
    try:
        provider = build_provider("stub", settings=Settings())
        assert isinstance(provider, _StubProvider)
    finally:
        registry_mod._PROVIDER_FACTORIES.pop("stub", None)


def test_build_provider_uses_config_default_when_name_omitted(monkeypatch):
    registry_mod.register_provider("stub", lambda settings: _StubProvider())
    monkeypatch.setenv("CADLESS_LLM_PROVIDER", "stub")
    try:
        provider = build_provider(settings=Settings())
        assert isinstance(provider, _StubProvider)
    finally:
        registry_mod._PROVIDER_FACTORIES.pop("stub", None)


def test_build_provider_unknown_raises_clear_error():
    with pytest.raises(ValueError, match="unknown.*provider|provider.*unknown|nope"):
        build_provider("nope-provider", settings=Settings())


# --- config fields ---------------------------------------------------------


def test_settings_gains_llm_fields_with_defaults():
    s = Settings()
    assert s.llm_provider == "anthropic"
    assert s.codegen_model == "sonnet-4-6"
    assert s.orchestrator_model.startswith("opus")


def test_settings_llm_fields_env_overridable(monkeypatch):
    # Deliberately not the default provider, so this still proves the override.
    monkeypatch.setenv("CADLESS_LLM_PROVIDER", "openai")
    monkeypatch.setenv("CADLESS_ORCHESTRATOR_MODEL", "opus-4-7")
    monkeypatch.setenv("CADLESS_CODEGEN_MODEL", "haiku-4-5")
    s = Settings()
    assert s.llm_provider == "openai"
    assert s.orchestrator_model == "opus-4-7"
    assert s.codegen_model == "haiku-4-5"
