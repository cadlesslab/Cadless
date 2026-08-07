"""Transcript compaction / rolling synopsis.

Independent session hygiene: when a session's neutral ``history`` grows past a
configurable threshold, the OLDER turns are folded into a single rolling synopsis
message while the most recent N turns stay verbatim, so the agent's context stays
bounded regardless of session length. The ``script_versions`` chain (the durable
code source of truth) is never touched — compaction only rewrites the conversational
transcript fed to the model.

Offline only: deterministic fake provider (no Bedrock); the compaction *policy*
(when to compact, what is kept verbatim vs summarised, failure fallback) is unit
tested separately from the LLM call.
"""

import asyncio

from cadless.compaction import (
    compact_history,
    needs_compaction,
    summarize_messages,
)
from cadless.config import settings as base_settings
from cadless.llm.providers import StreamChunk
from cadless.llm.providers.fake import FakeChatProvider
from cadless.llm.types import ContentBlock, Message, StreamEvent


def _msg(role: str, text: str) -> Message:
    return Message(role=role, content=[ContentBlock.of_text(text)])


def _turns(n: int) -> list[Message]:
    """n user/assistant pairs => 2n messages."""
    out: list[Message] = []
    for i in range(n):
        out.append(_msg("user", f"user message {i}"))
        out.append(_msg("assistant", f"assistant reply {i}"))
    return out


def _synopsis_provider(
    text: str = "SYNOPSIS: building a plate with a hole, d=6.",
) -> FakeChatProvider:
    return FakeChatProvider(
        script=[StreamChunk(event=StreamEvent.TEXT_DELTA, payload={"text": text})]
    )


# -- policy: when to compact -------------------------------------------------


def test_needs_compaction_false_below_threshold():
    cfg = base_settings.model_copy(
        update={"transcript_compact_threshold": 10, "transcript_keep_recent": 4}
    )
    assert needs_compaction(_turns(3), config=cfg) is False  # 6 messages <= 10


def test_needs_compaction_true_above_threshold():
    cfg = base_settings.model_copy(
        update={"transcript_compact_threshold": 10, "transcript_keep_recent": 4}
    )
    assert needs_compaction(_turns(8), config=cfg) is True  # 16 messages > 10


# -- short session: returned unchanged (purely additive) ---------------------


def test_short_history_returned_unchanged():
    cfg = base_settings.model_copy(
        update={"transcript_compact_threshold": 10, "transcript_keep_recent": 4}
    )
    provider = _synopsis_provider()
    history = _turns(3)  # 6 messages, below threshold

    out = asyncio.run(compact_history(history, provider, config=cfg))

    assert out == history  # identical, no summarization
    assert provider.calls == []  # the LLM was never called


# -- long session: older turns -> synopsis, recent N verbatim, bounded -------


def test_long_history_compacted_to_synopsis_plus_recent():
    cfg = base_settings.model_copy(
        update={"transcript_compact_threshold": 10, "transcript_keep_recent": 4}
    )
    provider = _synopsis_provider("SYNOPSIS: plate with hole, length=40, hole d=6.")
    history = _turns(8)  # 16 messages

    out = asyncio.run(compact_history(history, provider, config=cfg))

    # synopsis (1) + the most recent transcript_keep_recent messages kept verbatim
    assert len(out) == 1 + cfg.transcript_keep_recent
    # bounded: strictly smaller than the input
    assert len(out) < len(history)
    # recent tail is verbatim (object-equal to the original tail)
    assert out[1:] == history[-cfg.transcript_keep_recent :]
    # the first message is the rolling synopsis carrying the summary text
    synopsis = out[0]
    assert synopsis.content[0].text is not None
    assert "SYNOPSIS" in synopsis.content[0].text
    # the LLM was invoked exactly once to produce the synopsis
    assert len(provider.calls) == 1


def test_compaction_is_bounded_regardless_of_length():
    cfg = base_settings.model_copy(
        update={"transcript_compact_threshold": 10, "transcript_keep_recent": 4}
    )
    provider = _synopsis_provider()
    short_long = asyncio.run(compact_history(_turns(8), provider, config=cfg))
    very_long = asyncio.run(compact_history(_turns(50), provider, config=cfg))
    # both bound to synopsis + keep_recent regardless of input size
    assert len(short_long) == len(very_long) == 1 + cfg.transcript_keep_recent


def test_summarize_messages_uses_provider_and_preserves_facts_prompt():
    provider = _synopsis_provider("a summary")
    older = _turns(3)
    text = summarize_messages(older, provider, config=base_settings)
    assert text == "a summary"
    # the summarization prompt frames key-fact preservation (part, params, decisions)
    assert provider.last_complete_system is not None
    assert "summar" in provider.last_complete_system.lower()
    # the older transcript content is fed into the user payload
    assert provider.last_complete_user is not None
    assert "user message 0" in provider.last_complete_user


# -- script_versions chain untouched -----------------------------------------


def test_compaction_does_not_touch_script_versions():
    """Compaction must never alter the durable script_versions chain.

    The function operates purely on the neutral transcript list; it has no Store
    handle and cannot persist or delete versions. We assert it returns a new list
    without mutating the input messages (the transcript), proving it cannot touch
    persisted versions.
    """
    cfg = base_settings.model_copy(
        update={"transcript_compact_threshold": 10, "transcript_keep_recent": 4}
    )
    provider = _synopsis_provider()
    history = _turns(8)
    original_snapshot = [m.model_copy(deep=True) for m in history]

    compact_history_result = asyncio.run(compact_history(history, provider, config=cfg))

    # input list is not mutated in place
    assert history == original_snapshot
    assert compact_history_result is not history


# -- failure fallback: never crash the turn ----------------------------------


class _BoomProvider(FakeChatProvider):
    def complete(self, *, model: str, system: str, user: str) -> str:
        raise RuntimeError("summarizer down")


def test_summarization_failure_falls_back_to_safe_truncation():
    cfg = base_settings.model_copy(
        update={"transcript_compact_threshold": 10, "transcript_keep_recent": 4}
    )
    provider = _BoomProvider()
    history = _turns(8)

    out = asyncio.run(compact_history(history, provider, config=cfg))

    # best-effort: falls back to a safe truncation (the recent tail), bounded,
    # never raising and never exceeding the verbatim keep window
    assert len(out) <= cfg.transcript_keep_recent
    assert out == history[-cfg.transcript_keep_recent :]
