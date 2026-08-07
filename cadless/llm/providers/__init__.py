"""Concrete :class:`~cadless.llm.provider.ChatProvider` implementations.

Each module here translates one vendor's wire protocol to/from the neutral
:mod:`cadless.llm.types`. Importing this package registers the bundled
providers (``bedrock``, ``anthropic``, ``openai``, ``fake``) in
:mod:`cadless.llm.registry`.

Streaming carries payloads via :class:`StreamChunk`: a neutral
:class:`~cadless.llm.types.StreamEvent` paired with its out-of-band data
(text/thinking deltas, tool ids + partial JSON, stop reason, usage). The
:class:`~cadless.llm.provider.ChatProvider` protocol's ``stream_turn`` is
typed ``Iterator[StreamEvent]``; chunks expose ``.event`` for that boundary plus
``.payload`` for the data the agent loop and SSE layer consume.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from cadless.llm.types import StreamEvent


@dataclass(frozen=True, slots=True)
class StreamChunk:
    """One streamed event plus its out-of-band payload.

    ``event`` is the neutral boundary; ``payload`` is a small dict whose keys
    depend on the event (e.g. ``{"text": ...}`` for deltas, ``{"id", "name",
    "input", "block"}`` for ``TOOL_USE_STOP``, ``{"stop_reason"}`` for
    ``TURN_DELTA``, ``{"input_tokens", "output_tokens"}`` for ``USAGE``).
    """

    event: StreamEvent
    payload: dict[str, Any] = field(default_factory=dict)


def parse_partial_json(text: str) -> dict:
    """Parse an accumulated tool-input JSON stream, tolerating truncation.

    Every adapter accumulates tool input as partial-JSON fragments; an
    incomplete stream (turn cut off mid-call) surfaces the raw fragment under
    ``__partial_json__`` rather than raising, so the loop can still
    inspect/replay it.
    """
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"__partial_json__": text}


# Importing the concrete modules runs their ``register_provider`` side effects.
from cadless.llm.providers import anthropic as _anthropic  # noqa: E402,F401
from cadless.llm.providers import bedrock as _bedrock  # noqa: E402,F401
from cadless.llm.providers import fake as _fake  # noqa: E402,F401
from cadless.llm.providers import openai as _openai  # noqa: E402,F401

__all__ = ["StreamChunk", "parse_partial_json"]
