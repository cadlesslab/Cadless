"""Anthropic Messages API adapter.

Maps the Messages ``stream=True`` wire protocol onto the neutral
:mod:`cadless.llm.types` streaming vocabulary, and offers a single-shot
``complete`` over the same path. The SDK client is created lazily so importing
this module — and running the offline unit tests — needs no API key
(``ANTHROPIC_API_KEY`` is resolved by the SDK at first call).

Wire-event mapping (Messages stream → :class:`StreamEvent`):

====================================  ===============================
Messages event                        neutral event
====================================  ===============================
``message_start``                     ``TURN_START``
``content_block_start`` (tool_use)    ``TOOL_USE_START`` ``{id, name}``
``content_block_delta`` (text)        ``TEXT_DELTA`` ``{text}``
``content_block_delta`` (thinking)    ``THINKING_DELTA`` ``{text}``
``content_block_delta`` (input_json)  ``TOOL_INPUT_DELTA`` ``{partial_json}``
``content_block_stop`` (thinking)     ``THINKING_STOP`` ``{text, block}``
``content_block_stop`` (tool_use)     ``TOOL_USE_STOP`` ``{id, name, input, block}``
``message_delta``                     ``TURN_DELTA`` ``{stop_reason}`` + ``USAGE``
(end of stream)                       ``TURN_STOP``
====================================  ===============================

Like the Bedrock adapter, ``tool_use`` input arrives as partial-JSON fragments
that are accumulated and parsed at ``TOOL_USE_STOP``; ``thinking`` and
``tool_use`` blocks are tagged ``provider="anthropic"`` with a verbatim
``provider_raw`` payload so signed thinking replays losslessly. ``embed``
raises :class:`EmbeddingsUnsupported` — Anthropic has no embeddings API — which
the RAG/distill callers treat as a clean skip (ADR-0002).
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence

from cadless.config import Settings
from cadless.config import settings as default_settings
from cadless.llm.provider import EmbeddingsUnsupported
from cadless.llm.registry import register_provider
from cadless.llm.types import (
    Capabilities,
    ContentBlock,
    Message,
    StopReason,
    StreamEvent,
    ToolDef,
    TurnParams,
)

PROVIDER_NAME = "anthropic"

# Config slugs -> Anthropic API model ids. Same slugs as model_profiles.PROFILES
# so CADLESS_ORCHESTRATOR_MODEL / CADLESS_CODEGEN_MODEL keep working unchanged when
# the provider flips; raw ``claude-*`` API ids pass through for models not yet
# slugged.
_API_MODEL_IDS: dict[str, str] = {
    "sonnet-4-6": "claude-sonnet-4-6",
    "sonnet-4-5": "claude-sonnet-4-5",
    "haiku-4-5": "claude-haiku-4-5",
    "opus-4-8": "claude-opus-4-8",
    "opus-4-7": "claude-opus-4-7",
    "opus-4-6": "claude-opus-4-6",
}

# Messages API stop_reason strings -> neutral StopReason (unknown -> END_TURN).
_STOP_REASONS: dict[str, StopReason] = {
    "end_turn": StopReason.END_TURN,
    "tool_use": StopReason.TOOL_USE,
    "max_tokens": StopReason.MAX_TOKENS,
    "stop_sequence": StopReason.STOP_SEQUENCE,
}


def _resolve_api_model(model: str) -> str:
    """Slug -> API model id; raw ``claude-*`` ids pass through; fail fast otherwise."""
    if model in _API_MODEL_IDS:
        return _API_MODEL_IDS[model]
    if model.startswith("claude-"):
        return model
    raise KeyError(
        f"unknown model {model!r} for the anthropic provider; use one of "
        f"{sorted(_API_MODEL_IDS)} or a raw 'claude-*' API model id"
    )


class AnthropicChatProvider:
    """Messages API adapter implementing the :class:`ChatProvider` protocol."""

    def __init__(self, config: Settings | None = None, client=None) -> None:
        self._cfg = config or default_settings
        self._client = client  # injectable for tests; otherwise lazy-created

    @property
    def client(self):
        if self._client is None:
            import anthropic  # local import: no SDK dep at module import time

            # api_key resolves from ANTHROPIC_API_KEY; the SDK's built-in retry
            # covers 429/5xx/timeouts like the Bedrock adapter's explicit loop.
            # The bedrock_* knobs double as the neutral generation defaults.
            self._client = anthropic.Anthropic(max_retries=self._cfg.bedrock_max_retries)
        return self._client

    # -- request building ---------------------------------------------------

    def _build_request(
        self,
        *,
        model: str,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolDef],
        params: TurnParams,
    ) -> dict:
        # Extended thinking requires temperature == 1 (the API rejects any other
        # value), mirroring the Bedrock adapter.
        temperature = (
            1.0
            if params.thinking
            else (
                self._cfg.bedrock_temperature if params.temperature is None else params.temperature
            )
        )
        body: dict = {
            "model": _resolve_api_model(model),
            "max_tokens": params.max_tokens or self._cfg.bedrock_max_tokens,
            "temperature": temperature,
            "messages": _messages_to_anthropic(messages),
            "stream": True,
        }
        if system:
            body["system"] = system
        if params.stop_sequences:
            body["stop_sequences"] = list(params.stop_sequences)
        if tools:
            body["tools"] = [_tool_to_anthropic(t) for t in tools]
            if params.tool_choice == "any":
                body["tool_choice"] = {"type": "any"}
            elif params.tool_choice == "auto":
                body["tool_choice"] = {"type": "auto"}
        if params.thinking:
            budget = params.thinking_budget_tokens or 1024
            body["thinking"] = {"type": "enabled", "budget_tokens": budget}
        return body

    # -- streaming ----------------------------------------------------------

    def stream_turn(
        self,
        *,
        model: str,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolDef],
        params: TurnParams,
    ) -> Iterator[StreamChunk]:
        body = self._build_request(
            model=model, system=system, messages=messages, tools=tools, params=params
        )
        stream = self.client.messages.create(**body)
        yield from _translate_stream(stream)

    # -- capabilities + single-shot ----------------------------------------

    def capabilities(self, model: str) -> Capabilities:
        # All mapped slugs are Claude models: extended thinking and constrained
        # tool_choice are both supported (mirrors the Bedrock adapter).
        return Capabilities(
            supports_thinking=True,
            supports_tool_choice=True,
            max_output_tokens=self._cfg.bedrock_max_tokens,
        )

    def complete(
        self,
        *,
        model: str,
        system: str,
        user: str,
        temperature: float | None = None,
    ) -> str:
        """Run one turn and return the concatenated assistant text."""
        chunks = self.stream_turn(
            model=model,
            system=system,
            messages=[Message(role="user", content=[ContentBlock.of_text(user)])],
            tools=[],
            params=TurnParams(temperature=temperature),
        )
        return "".join(
            c.payload.get("text", "") for c in chunks if c.event == StreamEvent.TEXT_DELTA
        )

    # -- embeddings ---------------------------------------------------------

    def embed(self, text: str | Sequence[str]) -> list[float] | list[list[float]]:
        """Anthropic has no embeddings API — signal the gap with the typed error.

        Raised BEFORE any client/SDK work so callers get the clean skip
        (``rag.retrieve_grounding`` / ``distill.auto_distill`` catch it) without
        credentials or the SDK installed.
        """
        raise EmbeddingsUnsupported(PROVIDER_NAME)


# --- neutral -> anthropic request encoders ----------------------------------


def _messages_to_anthropic(messages: Sequence[Message]) -> list[dict]:
    out: list[dict] = []
    for message in messages:
        content = [enc for b in message.content if (enc := _block_to_anthropic(b)) is not None]
        if content:  # a message emptied by dropped blocks is omitted entirely
            out.append({"role": message.role, "content": content})
    return out


def _block_to_anthropic(block: ContentBlock) -> dict | None:
    # Verbatim replay: a block carrying provider_raw from anthropic round-trips
    # unchanged (e.g. signed thinking blocks).
    if block.provider == PROVIDER_NAME and block.provider_raw is not None:
        return block.provider_raw
    if block.kind == "text":
        return {"type": "text", "text": block.text or ""}
    if block.kind == "thinking":
        # Foreign/unsigned thinking cannot be replayed to the Messages API (it
        # validates thinking signatures); prior-turn thinking is advisory, so it
        # is dropped rather than re-encoded.
        return None
    if block.kind == "tool_use":
        return {
            "type": "tool_use",
            "id": block.id,
            "name": block.name,
            "input": block.input or {},
        }
    if block.kind == "tool_result":
        return {
            "type": "tool_result",
            "tool_use_id": block.tool_use_id,
            "content": block.content or "",
            "is_error": block.is_error,
        }
    raise ValueError(f"unsupported block kind: {block.kind!r}")


def _tool_to_anthropic(tool: ToolDef) -> dict:
    return {
        "name": tool.name,
        "description": tool.description,
        "input_schema": tool.input_schema,
    }


# --- anthropic stream -> neutral chunks --------------------------------------


def _translate_stream(stream) -> Iterator[StreamChunk]:
    from cadless.llm.providers import StreamChunk

    input_tokens = 0
    # Per-content-block accumulation state, keyed by the event ``index``.
    tool_state: dict[int, dict] = {}
    thinking_state: dict[int, dict] = {}

    for raw in stream:
        # SDK events are pydantic models mirroring the wire JSON; tests feed
        # plain dicts. Normalize once so the mapping below reads the wire shape.
        event = raw if isinstance(raw, dict) else raw.model_dump()
        etype = event.get("type", "")

        if etype == "message_start":
            usage = (event.get("message") or {}).get("usage") or {}
            input_tokens = usage.get("input_tokens", 0)
            yield StreamChunk(StreamEvent.TURN_START)

        elif etype == "content_block_start":
            idx = event.get("index", 0)
            cb = event.get("content_block") or {}
            if cb.get("type") == "tool_use":
                tool_state[idx] = {"id": cb.get("id"), "name": cb.get("name"), "input_json": ""}
                yield StreamChunk(
                    StreamEvent.TOOL_USE_START,
                    {"id": cb.get("id"), "name": cb.get("name")},
                )
            elif cb.get("type") == "thinking":
                thinking_state[idx] = {"text": "", "signature": None}
            elif cb.get("type") == "redacted_thinking":
                # Opaque server-side thinking: no deltas stream; keep the verbatim
                # block so it replays unchanged.
                thinking_state[idx] = {"text": "", "signature": None, "redacted": cb}

        elif etype == "content_block_delta":
            idx = event.get("index", 0)
            delta = event.get("delta") or {}
            dtype = delta.get("type", "")
            if dtype == "text_delta":
                yield StreamChunk(StreamEvent.TEXT_DELTA, {"text": delta.get("text", "")})
            elif dtype == "thinking_delta":
                state = thinking_state.setdefault(idx, {"text": "", "signature": None})
                state["text"] += delta.get("thinking", "")
                yield StreamChunk(StreamEvent.THINKING_DELTA, {"text": delta.get("thinking", "")})
            elif dtype == "signature_delta":
                # Signature arrives as its own delta after the thinking text;
                # retained so the replayed block stays valid for the model.
                state = thinking_state.setdefault(idx, {"text": "", "signature": None})
                state["signature"] = delta.get("signature")
            elif dtype == "input_json_delta":
                partial = delta.get("partial_json", "")
                tool_state.setdefault(idx, {"id": None, "name": None, "input_json": ""})
                tool_state[idx]["input_json"] += partial
                yield StreamChunk(StreamEvent.TOOL_INPUT_DELTA, {"partial_json": partial})

        elif etype == "content_block_stop":
            idx = event.get("index", 0)
            thinking = thinking_state.pop(idx, None)
            if thinking is not None:
                if "redacted" in thinking:
                    provider_raw = thinking["redacted"]
                else:
                    # provider_raw is the verbatim Messages thinking block, so it
                    # replays back to Anthropic unchanged (signature preserved).
                    provider_raw = {"type": "thinking", "thinking": thinking["text"]}
                    if thinking["signature"] is not None:
                        provider_raw["signature"] = thinking["signature"]
                block = ContentBlock.of_thinking(
                    thinking["text"], provider=PROVIDER_NAME, provider_raw=provider_raw
                )
                yield StreamChunk(
                    StreamEvent.THINKING_STOP,
                    {"text": thinking["text"], "block": block},
                )
            state = tool_state.pop(idx, None)
            if state is not None:
                parsed = parse_partial_json(state["input_json"])
                block = ContentBlock.of_tool_use(
                    id=state["id"] or "",
                    name=state["name"] or "",
                    input=parsed,
                    provider=PROVIDER_NAME,
                    provider_raw={
                        "type": "tool_use",
                        "id": state["id"],
                        "name": state["name"],
                        "input": parsed,
                    },
                )
                yield StreamChunk(
                    StreamEvent.TOOL_USE_STOP,
                    {
                        "id": state["id"],
                        "name": state["name"],
                        "input": parsed,
                        "block": block,
                    },
                )

        elif etype == "message_delta":
            raw_reason = (event.get("delta") or {}).get("stop_reason") or ""
            yield StreamChunk(
                StreamEvent.TURN_DELTA,
                {"stop_reason": _STOP_REASONS.get(raw_reason, StopReason.END_TURN)},
            )
            # input_tokens arrived on message_start; output_tokens (cumulative)
            # arrives here — emit the one USAGE event with both.
            usage = event.get("usage") or {}
            yield StreamChunk(
                StreamEvent.USAGE,
                {
                    "input_tokens": input_tokens,
                    "output_tokens": usage.get("output_tokens", 0),
                },
            )

        elif etype == "error":
            raise RuntimeError(f"anthropic stream error: {event.get('error')}")

        # ``ping`` / ``message_stop`` carry nothing the neutral vocabulary needs.

    yield StreamChunk(StreamEvent.TURN_STOP)


def _factory(settings: Settings) -> AnthropicChatProvider:
    return AnthropicChatProvider(config=settings)


register_provider(PROVIDER_NAME, _factory)


# Imported at the bottom to avoid a circular import with the package
# ``__init__`` that defines ``StreamChunk`` and imports this module.
from cadless.llm.providers import (  # noqa: E402,F401
    StreamChunk,
    parse_partial_json,
)
