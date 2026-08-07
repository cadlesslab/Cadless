"""AWS Bedrock Converse adapter.

Maps the Bedrock ``converse_stream`` wire protocol onto the neutral
:mod:`cadless.llm.types` streaming vocabulary, and offers a single-shot
``complete`` over the same path (the drop-in replacement for the old
``cadless.bedrock.BedrockClient.generate``). The boto3 client is created
lazily so importing this module — and running the offline unit tests — needs no
AWS credentials.

Wire-event mapping (``converse_stream`` → :class:`StreamEvent`):

================================  ===============================
Bedrock event                     neutral event
================================  ===============================
``messageStart``                  ``TURN_START``
``contentBlockStart`` (toolUse)   ``TOOL_USE_START`` ``{id, name}``
``contentBlockDelta`` (text)      ``TEXT_DELTA`` ``{text}``
``contentBlockDelta`` (reasoning) ``THINKING_DELTA`` ``{text}``
``contentBlockDelta`` (toolUse)   ``TOOL_INPUT_DELTA`` ``{partial_json}``
``contentBlockStop`` (toolUse)    ``TOOL_USE_STOP`` ``{id, name, input, block}``
``messageStop``                   ``TURN_DELTA`` ``{stop_reason}``
``metadata`` (usage)              ``USAGE`` ``{input_tokens, output_tokens}``
(end of stream)                   ``TURN_STOP``
================================  ===============================

``tool_use`` input arrives as a stream of partial-JSON fragments; the adapter
accumulates them and emits the parsed object on ``TOOL_USE_STOP``. ``thinking``
and ``tool_use`` blocks are tagged ``provider="bedrock"`` and carry a
``provider_raw`` payload so they can be replayed verbatim to Bedrock later.
"""

from __future__ import annotations

import json
import time
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
from cadless.model_profiles import resolve_model_id

PROVIDER_NAME = "bedrock"

# Bedrock stopReason strings -> neutral StopReason.
_STOP_REASONS: dict[str, StopReason] = {
    "end_turn": StopReason.END_TURN,
    "tool_use": StopReason.TOOL_USE,
    "max_tokens": StopReason.MAX_TOKENS,
    "stop_sequence": StopReason.STOP_SEQUENCE,
}

_RETRYABLE = {
    "ThrottlingException",
    "ModelTimeoutException",
    "ServiceUnavailableException",
    "InternalServerException",
}


class BedrockChatProvider:
    """Converse adapter implementing the :class:`ChatProvider` protocol."""

    def __init__(self, config: Settings | None = None, client=None) -> None:
        self._cfg = config or default_settings
        self._client = client  # injectable for tests; otherwise lazy-created

    @property
    def client(self):
        if self._client is None:
            import boto3  # local import: no AWS dep at module import time

            self._client = boto3.client("bedrock-runtime", region_name=self._cfg.aws_region)
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
        # Extended thinking requires temperature == 1 (Bedrock rejects any other
        # value with a ValidationException), so it overrides the configured/explicit
        # temperature whenever thinking is enabled.
        temperature = (
            1.0
            if params.thinking
            else (
                self._cfg.bedrock_temperature if params.temperature is None else params.temperature
            )
        )
        body: dict = {
            "modelId": resolve_model_id(model),
            "messages": [_message_to_bedrock(m) for m in messages],
            "inferenceConfig": {
                "maxTokens": params.max_tokens or self._cfg.bedrock_max_tokens,
                "temperature": temperature,
            },
        }
        if system:
            body["system"] = [{"text": system}]
        if params.stop_sequences:
            body["inferenceConfig"]["stopSequences"] = list(params.stop_sequences)
        if tools:
            tool_config: dict = {"tools": [_tool_to_bedrock(t) for t in tools]}
            if params.tool_choice == "any":
                tool_config["toolChoice"] = {"any": {}}
            elif params.tool_choice == "auto":
                tool_config["toolChoice"] = {"auto": {}}
            body["toolConfig"] = tool_config
        if params.thinking:
            budget = params.thinking_budget_tokens or 1024
            body["additionalModelRequestFields"] = {
                "thinking": {"type": "enabled", "budget_tokens": budget}
            }
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
        response = self._converse_stream_with_retry(body)
        yield from _translate_stream(response["stream"])

    def _converse_stream_with_retry(self, body: dict) -> dict:
        from botocore.exceptions import ClientError

        last_exc: Exception | None = None
        for attempt in range(self._cfg.bedrock_max_retries):
            try:
                return self.client.converse_stream(**body)
            except ClientError as exc:  # pragma: no cover - needs live throttling
                code = exc.response.get("Error", {}).get("Code", "")
                last_exc = exc
                if code not in _RETRYABLE:
                    raise
                time.sleep(min(2**attempt, 8))
        raise last_exc  # type: ignore[misc]

    # -- capabilities + single-shot ----------------------------------------

    def capabilities(self, model: str) -> Capabilities:
        # All currently-mapped slugs are Claude models on Bedrock: extended
        # thinking and constrained tool_choice are both supported.
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
        """Run one turn and return the concatenated assistant text.

        Drop-in for the old single-shot ``BedrockClient.generate(...).text``.
        ``temperature`` (when given) overrides ``bedrock_temperature`` for this
        call only — used by the best-of-N fan-out; ``None`` keeps the
        configured default.
        """
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
        """Embed ``text`` via Bedrock Titan Text Embeddings V2 (``invoke_model``).

        Reuses the same lazily-created bedrock-runtime client / region / creds as
        the Converse path. Titan embeds one text per call, so a batch maps to one
        ``invoke_model`` per input, returning vectors aligned to the input order.
        """
        if isinstance(text, str):
            return self._embed_one(text)
        return [self._embed_one(t) for t in text]

    def _embed_one(self, text: str) -> list[float]:
        body = json.dumps({"inputText": text, "dimensions": self._cfg.embed_dimensions})
        response = self.client.invoke_model(modelId=self._cfg.embed_model_id, body=body)
        payload = json.loads(response["body"].read())
        return [float(x) for x in payload["embedding"]]


# --- neutral <- bedrock request encoders -----------------------------------


def _message_to_bedrock(message: Message) -> dict:
    return {
        "role": message.role,
        "content": [_block_to_bedrock(b) for b in message.content],
    }


def _block_to_bedrock(block: ContentBlock) -> dict:
    # Verbatim replay: a block carrying provider_raw from bedrock round-trips
    # unchanged (e.g. signed thinking blocks).
    if block.provider == PROVIDER_NAME and block.provider_raw is not None:
        return block.provider_raw
    if block.kind == "text":
        return {"text": block.text or ""}
    if block.kind == "thinking":
        return {"reasoningContent": {"reasoningText": {"text": block.text or ""}}}
    if block.kind == "tool_use":
        return {
            "toolUse": {
                "toolUseId": block.id,
                "name": block.name,
                "input": block.input or {},
            }
        }
    if block.kind == "tool_result":
        return {
            "toolResult": {
                "toolUseId": block.tool_use_id,
                "content": [{"text": block.content or ""}],
                "status": "error" if block.is_error else "success",
            }
        }
    raise ValueError(f"unsupported block kind: {block.kind!r}")


def _tool_to_bedrock(tool: ToolDef) -> dict:
    return {
        "toolSpec": {
            "name": tool.name,
            "description": tool.description,
            "inputSchema": {"json": tool.input_schema},
        }
    }


# --- bedrock stream -> neutral chunks ---------------------------------------


def _translate_stream(stream) -> Iterator[StreamChunk]:
    from cadless.llm.providers import StreamChunk

    # Per-content-block accumulation state, keyed by contentBlockIndex.
    tool_state: dict[int, dict] = {}
    # Reasoning blocks accumulate their streamed text + (trailing) signature so a
    # neutral ``thinking`` block with verbatim ``provider_raw`` is emitted at stop.
    reasoning_state: dict[int, dict] = {}

    for event in stream:
        if "messageStart" in event:
            yield StreamChunk(StreamEvent.TURN_START)

        elif "contentBlockStart" in event:
            start = event["contentBlockStart"]
            idx = start.get("contentBlockIndex", 0)
            tool_use = start.get("start", {}).get("toolUse")
            if tool_use:
                tool_state[idx] = {
                    "id": tool_use.get("toolUseId"),
                    "name": tool_use.get("name"),
                    "input_json": "",
                }
                yield StreamChunk(
                    StreamEvent.TOOL_USE_START,
                    {"id": tool_use.get("toolUseId"), "name": tool_use.get("name")},
                )

        elif "contentBlockDelta" in event:
            cbd = event["contentBlockDelta"]
            idx = cbd.get("contentBlockIndex", 0)
            delta = cbd.get("delta", {})
            if "text" in delta:
                yield StreamChunk(StreamEvent.TEXT_DELTA, {"text": delta["text"]})
            elif "reasoningContent" in delta:
                rc = delta["reasoningContent"]
                state = reasoning_state.setdefault(idx, {"text": "", "signature": None})
                if "text" in rc:
                    state["text"] += rc["text"]
                    yield StreamChunk(StreamEvent.THINKING_DELTA, {"text": rc["text"]})
                if "signature" in rc:
                    # Signature arrives as its own delta after the reasoning text;
                    # retained so the replayed block stays valid for the model.
                    state["signature"] = rc["signature"]
            elif "toolUse" in delta:
                partial = delta["toolUse"].get("input", "")
                tool_state.setdefault(idx, {"id": None, "name": None, "input_json": ""})
                tool_state[idx]["input_json"] += partial
                yield StreamChunk(StreamEvent.TOOL_INPUT_DELTA, {"partial_json": partial})

        elif "contentBlockStop" in event:
            idx = event["contentBlockStop"].get("contentBlockIndex", 0)
            reasoning = reasoning_state.pop(idx, None)
            if reasoning is not None:
                reasoning_text = {"text": reasoning["text"]}
                if reasoning["signature"] is not None:
                    reasoning_text["signature"] = reasoning["signature"]
                # provider_raw is the verbatim Converse reasoningContent block, so
                # it replays back to Bedrock unchanged (signature preserved).
                provider_raw = {"reasoningContent": {"reasoningText": reasoning_text}}
                block = ContentBlock.of_thinking(
                    reasoning["text"],
                    provider=PROVIDER_NAME,
                    provider_raw=provider_raw,
                )
                yield StreamChunk(
                    StreamEvent.THINKING_STOP,
                    {"text": reasoning["text"], "block": block},
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
                        "toolUse": {
                            "toolUseId": state["id"],
                            "name": state["name"],
                            "input": parsed,
                        }
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

        elif "messageStop" in event:
            raw = event["messageStop"].get("stopReason", "")
            yield StreamChunk(
                StreamEvent.TURN_DELTA,
                {"stop_reason": _STOP_REASONS.get(raw, StopReason.END_TURN)},
            )

        elif "metadata" in event:
            usage = event["metadata"].get("usage", {})
            yield StreamChunk(
                StreamEvent.USAGE,
                {
                    "input_tokens": usage.get("inputTokens", 0),
                    "output_tokens": usage.get("outputTokens", 0),
                },
            )

    yield StreamChunk(StreamEvent.TURN_STOP)


def _factory(settings: Settings) -> BedrockChatProvider:
    return BedrockChatProvider(config=settings)


register_provider(PROVIDER_NAME, _factory)


# Imported at the bottom to avoid a circular import with the package
# ``__init__`` that defines ``StreamChunk`` and imports this module.
from cadless.llm.providers import (  # noqa: E402,F401
    StreamChunk,
    parse_partial_json,
)
