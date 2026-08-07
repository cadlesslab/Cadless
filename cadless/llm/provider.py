"""The ``ChatProvider`` protocol.

A provider translates between :mod:`cadless.llm.types` and a concrete vendor
API. This module is **vendor-free**; concrete providers (bedrock, anthropic,
openai, fake) live in their own modules and are wired in via
:mod:`cadless.llm.registry`.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Protocol, overload, runtime_checkable

from cadless.llm.types import (
    Capabilities,
    Message,
    StreamEvent,
    ToolDef,
    TurnParams,
)


class EmbeddingsUnsupported(RuntimeError):
    """Raised by :meth:`ChatProvider.embed` when the vendor has no embeddings API.

    Anthropic offers chat but no embeddings endpoint, yet the protocol requires
    ``embed`` — so an adapter signals the capability gap with this typed error
    instead of an opaque crash (ADR-0002). Callers that treat embeddings as
    optional (``rag.retrieve_grounding``, ``distill.auto_distill``) catch it and
    skip, preserving their purely-additive behaviour; direct callers surface it
    as-is.
    """

    def __init__(self, provider: str) -> None:
        self.provider = provider
        super().__init__(
            f"provider {provider!r} has no embeddings API; RAG/KB features that "
            "embed text are skipped for this provider"
        )


@runtime_checkable
class ChatProvider(Protocol):
    """A pluggable chat backend.

    Implementations stream a turn as neutral :class:`StreamEvent`s, report a
    model's :class:`Capabilities`, and offer a one-shot ``complete`` helper for
    simple text-in/text-out calls.
    """

    def stream_turn(
        self,
        *,
        model: str,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolDef],
        params: TurnParams,
    ) -> Iterator[StreamEvent]:
        """Stream one assistant turn as a sequence of neutral events."""
        ...

    def capabilities(self, model: str) -> Capabilities:
        """Report what ``model`` supports (thinking, tool_choice, max tokens)."""
        ...

    def complete(
        self,
        *,
        model: str,
        system: str,
        user: str,
        temperature: float | None = None,
    ) -> str:
        """Run one non-streaming turn and return the concatenated text.

        ``temperature`` overrides the configured default for this single call
        (used by the best-of-N fan-out, to raise diversity). ``None``
        keeps the provider/model default, so the legacy call path is unchanged.
        """
        ...

    @overload
    def embed(self, text: str) -> list[float]: ...
    @overload
    def embed(self, text: Sequence[str]) -> list[list[float]]: ...
    def embed(self, text: str | Sequence[str]) -> list[float] | list[list[float]]:
        """Embed ``text`` into a dense vector (the embedding capability).

        A single string returns one ``list[float]``; a sequence of strings returns
        a ``list[list[float]]`` aligned with the input order. Like ``stream_turn``
        this is vendor-neutral — the default backend is Bedrock Titan Text
        Embeddings V2, swappable via config. Foundation for the KB + RAG work
        (B2-B4).
        """
        ...
