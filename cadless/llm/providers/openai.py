"""OpenAI Chat Completions adapter.

Maps the Chat Completions ``stream=True`` chunk protocol onto the neutral
:mod:`cadless.llm.types` streaming vocabulary, offers a single-shot
``complete``, and implements ``embed`` via the Embeddings API
(``text-embedding-3-*``). The SDK client is created lazily so importing this
module — and running the offline unit tests — needs no API key
(``OPENAI_API_KEY`` is resolved by the SDK at first call).

Wire mapping (chat chunk → :class:`StreamEvent`):

==========================================  ===============================
Chat Completions chunk                      neutral event
==========================================  ===============================
first chunk                                 ``TURN_START``
``delta.content``                           ``TEXT_DELTA`` ``{text}``
``delta.tool_calls[i]`` (new index)         ``TOOL_USE_START`` ``{id, name}``
``delta.tool_calls[i].function.arguments``  ``TOOL_INPUT_DELTA`` ``{partial_json}``
index change / ``finish_reason``            ``TOOL_USE_STOP`` ``{id, name, input, block}``
``finish_reason``                           ``TURN_DELTA`` ``{stop_reason}``
``usage`` (final chunk)                     ``USAGE`` ``{input_tokens, output_tokens}``
(end of stream)                             ``TURN_STOP``
==========================================  ===============================

Vendor quirks kept INSIDE this module:

* Tool calls stream as *indexed fragments* — id/name on the first fragment for
  an index, argument text spread over later ones. The adapter accumulates per
  index and closes a call when the next index opens or the turn finishes.
* There is no exposed extended-thinking stream: ``capabilities`` reports
  ``supports_thinking=False``, so the agent loop never enables thinking here,
  and any prior-turn thinking blocks are dropped from replay.
* Reasoning models (``o*`` / ``gpt-5*``) reject sampling params, so
  ``temperature`` and ``stop`` are omitted for them; ``max_completion_tokens``
  is used for every model (``max_tokens`` is rejected by reasoning models).
* Tool results are top-level ``role="tool"`` messages, not content blocks; an
  ``is_error`` result is prefixed ``[tool error] `` because the API has no
  error-status field on tool messages.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Sequence

from cadless.config import Settings
from cadless.config import settings as default_settings
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
from cadless.model_profiles import PROFILES

PROVIDER_NAME = "openai"

# Chat Completions finish_reason strings -> neutral StopReason.
_STOP_REASONS: dict[str, StopReason] = {
    "stop": StopReason.END_TURN,
    "tool_calls": StopReason.TOOL_USE,
    "function_call": StopReason.TOOL_USE,
    "length": StopReason.MAX_TOKENS,
}

# Reasoning-model families that reject sampling params (temperature/stop).
_REASONING_MODEL = re.compile(r"^(o\d|gpt-5)")


def _check_model(model: str) -> str:
    """Pass raw OpenAI model ids through; fail fast on Bedrock/Claude slugs.

    The config defaults (``orchestrator_model``/``codegen_model``) are Claude
    slugs — flipping ``CADLESS_LLM_PROVIDER=openai`` without repointing them
    would otherwise surface as an opaque 404 from the API.
    """
    if model in PROFILES or model.startswith("claude-"):
        raise ValueError(
            f"model {model!r} is a Bedrock/Claude model; when "
            "CADLESS_LLM_PROVIDER=openai, set CADLESS_ORCHESTRATOR_MODEL / "
            "CADLESS_CODEGEN_MODEL to OpenAI model ids (e.g. 'gpt-4o')"
        )
    return model


class OpenAIChatProvider:
    """Chat Completions adapter implementing the :class:`ChatProvider` protocol."""

    def __init__(self, config: Settings | None = None, client=None) -> None:
        self._cfg = config or default_settings
        self._client = client  # injectable for tests; otherwise lazy-created

    @property
    def client(self):
        if self._client is None:
            import openai  # local import: no SDK dep at module import time

            # api_key resolves from OPENAI_API_KEY; the SDK's built-in retry
            # covers 429/5xx/timeouts like the Bedrock adapter's explicit loop.
            # The bedrock_* knobs double as the neutral generation defaults.
            self._client = openai.OpenAI(max_retries=self._cfg.bedrock_max_retries)
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
        model = _check_model(model)
        body: dict = {
            "model": model,
            "messages": _messages_to_openai(system, messages),
            "max_completion_tokens": params.max_tokens or self._cfg.bedrock_max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if not _REASONING_MODEL.match(model):
            body["temperature"] = (
                self._cfg.bedrock_temperature if params.temperature is None else params.temperature
            )
            if params.stop_sequences:
                body["stop"] = list(params.stop_sequences)
        if tools:
            body["tools"] = [_tool_to_openai(t) for t in tools]
            if params.tool_choice == "any":
                body["tool_choice"] = "required"
            elif params.tool_choice == "auto":
                body["tool_choice"] = "auto"
        # params.thinking is ignored on purpose: capabilities() reports no
        # thinking support, so the agent loop never enables it for this provider.
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
        stream = self.client.chat.completions.create(**body)
        yield from _translate_stream(stream)

    # -- capabilities + single-shot ----------------------------------------

    def capabilities(self, model: str) -> Capabilities:
        # Chat Completions exposes no extended-thinking stream (reasoning models
        # keep their reasoning server-side), so the loop must not enable it.
        return Capabilities(
            supports_thinking=False,
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
        """Embed ``text`` via the Embeddings API, input-aligned like the protocol.

        The ``text-embedding-3`` family accepts a ``dimensions`` override, which
        is passed from config so the KB vector store keeps one dimensionality
        across backends; other embedding models reject the parameter and get
        their native dimensionality.
        """
        inputs = [text] if isinstance(text, str) else list(text)
        kwargs: dict = {"model": self._cfg.openai_embed_model, "input": inputs}
        if self._cfg.openai_embed_model.startswith("text-embedding-3"):
            kwargs["dimensions"] = self._cfg.embed_dimensions
        raw = self.client.embeddings.create(**kwargs)
        resp = raw if isinstance(raw, dict) else raw.model_dump()
        rows = sorted(resp.get("data") or [], key=lambda d: d.get("index", 0))
        vectors = [[float(x) for x in row.get("embedding") or []] for row in rows]
        if isinstance(text, str):
            return vectors[0] if vectors else []
        return vectors


# --- neutral -> openai request encoders --------------------------------------


def _messages_to_openai(system: str, messages: Sequence[Message]) -> list[dict]:
    out: list[dict] = []
    if system:
        out.append({"role": "system", "content": system})
    for message in messages:
        if message.role == "assistant":
            out.extend(_assistant_to_openai(message))
        else:
            out.extend(_user_to_openai(message))
    return out


def _user_to_openai(message: Message) -> list[dict]:
    """One neutral user turn: tool results become top-level ``role="tool"``
    messages (in block order); the remaining text folds into one user message."""
    out: list[dict] = []
    texts: list[str] = []
    for block in message.content:
        if block.kind == "tool_result":
            content = block.content or ""
            if block.is_error:
                content = f"[tool error] {content}"
            out.append(
                {
                    "role": "tool",
                    "tool_call_id": block.tool_use_id,
                    "content": content,
                }
            )
        elif block.kind == "text":
            texts.append(block.text or "")
        else:
            raise ValueError(f"unsupported block kind in user message: {block.kind!r}")
    if texts:
        out.append({"role": "user", "content": "\n\n".join(texts)})
    return out


def _assistant_to_openai(message: Message) -> list[dict]:
    texts: list[str] = []
    tool_calls: list[dict] = []
    for block in message.content:
        if block.kind == "text":
            texts.append(block.text or "")
        elif block.kind == "thinking":
            # No thinking replay on Chat Completions — prior-turn thinking is
            # advisory and dropped (mirrors the anthropic adapter's rule).
            continue
        elif block.kind == "tool_use":
            # Verbatim replay when the call originally came from openai.
            if block.provider == PROVIDER_NAME and block.provider_raw is not None:
                tool_calls.append(block.provider_raw)
            else:
                tool_calls.append(
                    {
                        "id": block.id,
                        "type": "function",
                        "function": {
                            "name": block.name,
                            "arguments": json.dumps(block.input or {}),
                        },
                    }
                )
        else:
            raise ValueError(f"unsupported block kind in assistant message: {block.kind!r}")
    msg: dict = {"role": "assistant", "content": "\n\n".join(texts) if texts else None}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    if msg["content"] is None and not tool_calls:
        return []  # emptied by dropped blocks — omit the message entirely
    return [msg]


def _tool_to_openai(tool: ToolDef) -> dict:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.input_schema,
        },
    }


# --- openai stream -> neutral chunks -----------------------------------------


def _translate_stream(stream) -> Iterator[StreamChunk]:
    from cadless.llm.providers import StreamChunk

    started = False
    # One tool call accumulates at a time (fragments for an index arrive
    # contiguously); the open call closes when the next index opens or the turn
    # finishes.
    open_index: int | None = None
    open_call: dict = {}

    def _close_open() -> StreamChunk | None:
        nonlocal open_index, open_call
        if open_index is None:
            return None
        parsed = parse_partial_json(open_call.get("arguments", ""))
        provider_raw = {
            "id": open_call.get("id"),
            "type": "function",
            "function": {
                "name": open_call.get("name"),
                "arguments": open_call.get("arguments", ""),
            },
        }
        block = ContentBlock.of_tool_use(
            id=open_call.get("id") or "",
            name=open_call.get("name") or "",
            input=parsed,
            provider=PROVIDER_NAME,
            provider_raw=provider_raw,
        )
        chunk = StreamChunk(
            StreamEvent.TOOL_USE_STOP,
            {
                "id": open_call.get("id"),
                "name": open_call.get("name"),
                "input": parsed,
                "block": block,
            },
        )
        open_index, open_call = None, {}
        return chunk

    for raw in stream:
        # SDK chunks are pydantic models mirroring the wire JSON; tests feed
        # plain dicts. Normalize once so the mapping below reads the wire shape.
        chunk_d = raw if isinstance(raw, dict) else raw.model_dump()
        if not started:
            started = True
            yield StreamChunk(StreamEvent.TURN_START)

        choices = chunk_d.get("choices") or []
        if choices:
            choice = choices[0]
            delta = choice.get("delta") or {}
            text = delta.get("content")
            if text:
                yield StreamChunk(StreamEvent.TEXT_DELTA, {"text": text})
            for frag in delta.get("tool_calls") or []:
                idx = frag.get("index", 0)
                fn = frag.get("function") or {}
                if idx != open_index:
                    closed = _close_open()
                    if closed is not None:
                        yield closed
                    open_index = idx
                    open_call = {"id": frag.get("id"), "name": fn.get("name"), "arguments": ""}
                    yield StreamChunk(
                        StreamEvent.TOOL_USE_START,
                        {"id": open_call["id"], "name": open_call["name"]},
                    )
                else:
                    # Some gateways repeat id/name on later fragments — keep the
                    # first non-empty value.
                    open_call["id"] = open_call["id"] or frag.get("id")
                    open_call["name"] = open_call["name"] or fn.get("name")
                args = fn.get("arguments") or ""
                if args:
                    open_call["arguments"] += args
                    yield StreamChunk(StreamEvent.TOOL_INPUT_DELTA, {"partial_json": args})
            finish = choice.get("finish_reason")
            if finish:
                closed = _close_open()
                if closed is not None:
                    yield closed
                yield StreamChunk(
                    StreamEvent.TURN_DELTA,
                    {"stop_reason": _STOP_REASONS.get(finish, StopReason.END_TURN)},
                )

        # With stream_options.include_usage the final chunk carries usage and an
        # empty choices list.
        usage = chunk_d.get("usage")
        if usage:
            yield StreamChunk(
                StreamEvent.USAGE,
                {
                    "input_tokens": usage.get("prompt_tokens", 0),
                    "output_tokens": usage.get("completion_tokens", 0),
                },
            )

    closed = _close_open()  # a stream cut off mid-call still closes the call
    if closed is not None:
        yield closed
    yield StreamChunk(StreamEvent.TURN_STOP)


def _factory(settings: Settings) -> OpenAIChatProvider:
    return OpenAIChatProvider(config=settings)


register_provider(PROVIDER_NAME, _factory)


# Imported at the bottom to avoid a circular import with the package
# ``__init__`` that defines ``StreamChunk`` and imports this module.
from cadless.llm.providers import (  # noqa: E402,F401
    StreamChunk,
    parse_partial_json,
)
