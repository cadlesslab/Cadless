"""Scripted, fully-offline :class:`ChatProvider` for deterministic tests.

Mirrors the ``FakeGen`` pattern in ``tests/test_pipeline.py``: construct it with a
list of :class:`~cadless.llm.providers.StreamChunk`s and every ``stream_turn``
replays that exact sequence — no network, no AWS, no clock. This is what lets the
agent loop, tool dispatch, capability gating, and SSE mapping be unit-tested in
isolation.
"""

from __future__ import annotations

import hashlib
import struct
from collections.abc import Iterator, Sequence

from cadless.config import Settings
from cadless.config import settings as default_settings
from cadless.llm.registry import register_provider
from cadless.llm.types import (
    Capabilities,
    Message,
    StreamEvent,
    ToolDef,
    TurnParams,
)


class FakeChatProvider:
    """Replays a fixed list of stream chunks, deterministically.

    The ``script`` is replayed verbatim on every ``stream_turn`` call, so tests
    get identical events run-to-run. ``complete`` concatenates the ``text`` of
    every ``TEXT_DELTA`` chunk, matching the real adapters' single-shot helper.
    """

    def __init__(
        self,
        *,
        script: Sequence[StreamChunk] | None = None,
        config: Settings | None = None,
    ) -> None:
        self._script = list(script or [])
        self._cfg = config or default_settings
        self.calls: list[dict] = []
        # Last ``complete`` call's args, for tests that assert on the one-shot
        # summarization path where ``user`` carries the payload.
        self.last_complete_system: str | None = None
        self.last_complete_user: str | None = None

    def stream_turn(
        self,
        *,
        model: str,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolDef],
        params: TurnParams,
    ) -> Iterator[StreamChunk]:
        self.calls.append(
            {
                "model": model,
                "system": system,
                "messages": list(messages),
                "tools": list(tools),
                "params": params,
            }
        )
        yield from self._script

    def capabilities(self, model: str) -> Capabilities:
        # Permissive on purpose: tests exercising thinking/tool_choice paths
        # shouldn't be gated out by the fake.
        return Capabilities(
            supports_thinking=True, supports_tool_choice=True, max_output_tokens=4096
        )

    def complete(
        self,
        *,
        model: str,
        system: str,
        user: str,
        temperature: float | None = None,
    ) -> str:
        self.last_complete_system = system
        self.last_complete_user = user
        self.last_complete_temperature = temperature
        chunks = self.stream_turn(
            model=model,
            system=system,
            messages=[Message(role="user", content=[])],
            tools=[],
            params=TurnParams(),
        )
        return "".join(
            c.payload.get("text", "") for c in chunks if c.event == StreamEvent.TEXT_DELTA
        )

    def embed(self, text: str | Sequence[str]) -> list[float] | list[list[float]]:
        """Deterministic, hash-based pseudo-embeddings — no network, no AWS.

        Same dimensionality as the configured backend so downstream code (vector
        index, RAG) behaves identically offline. Identical text always yields the
        identical vector; distinct text yields a distinct vector. This is what lets
        B2-B4 be unit-tested without live Bedrock.
        """
        if isinstance(text, str):
            return self._embed_one(text)
        return [self._embed_one(t) for t in text]

    def _embed_one(self, text: str) -> list[float]:
        dims = self._cfg.embed_dimensions
        out: list[float] = []
        counter = 0
        # Stretch a sequence of digests over the requested dimensionality, mapping
        # each 4-byte word to a float in [0, 1) — fully determined by ``text``.
        while len(out) < dims:
            digest = hashlib.sha256(f"{text}\x00{counter}".encode()).digest()
            for i in range(0, len(digest), 4):
                if len(out) >= dims:
                    break
                word = struct.unpack(">I", digest[i : i + 4])[0]
                out.append(word / 0x1_0000_0000)
            counter += 1
        return out


def _factory(settings: Settings) -> FakeChatProvider:
    """Registry factory: an empty-script fake (tests inject their own script)."""
    return FakeChatProvider(config=settings)


register_provider("fake", _factory)


# Imported lazily at the bottom to avoid a circular import with the package
# ``__init__`` that defines ``StreamChunk`` and imports this module.
from cadless.llm.providers import StreamChunk  # noqa: E402,F401
