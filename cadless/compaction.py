"""Transcript compaction / rolling synopsis — session hygiene.

Long chat sessions grow the neutral ``history`` (list of
:class:`~cadless.llm.types.Message`) replayed into every turn unboundedly. This
module folds the OLDER turns of an over-long transcript into a single *rolling
synopsis* message while keeping the most recent N messages verbatim, so the agent's
context stays bounded regardless of session length.

Crucially, this operates **only on the conversational transcript** fed to the
model. The durable source of truth for CODE is the persisted ``script_versions``
chain, which this module never sees and never alters — it has no :class:`Store`
handle, takes a plain message list, and returns a new message list. The synopsis
replaces older verbose turns; recent turns stay exactly as they were.

The summary is produced via the provider/LLM seam
(:meth:`~cadless.llm.provider.ChatProvider.complete`) with a prompt framed to
preserve key facts (what part is being built, parameters, decisions) so the agent
does not lose the thread. The seam is injectable, so the compaction *policy* (when
to compact, what is kept verbatim vs summarised) is unit-tested independently of any
real LLM via the fake provider.

Best-effort: if summarisation fails, we fall back to a safe truncation (the recent
verbatim tail) rather than crashing the turn. Purely additive: a transcript at or
below the threshold is returned unchanged (no summarisation, no LLM call).
"""

from __future__ import annotations

import logging

from cadless.config import Settings, settings
from cadless.llm.provider import ChatProvider
from cadless.llm.types import ContentBlock, Message
from cadless.model_profiles import resolve_model_id

logger = logging.getLogger(__name__)

# System prompt for the rolling-synopsis summariser. Framed to preserve the key
# facts the agent needs to keep the thread: the part under construction, its
# parameters, and decisions made — not a verbatim retelling.
_SUMMARY_SYSTEM = (
    "You summarise the EARLIER part of a CAD design chat between a user and an "
    "assistant into a compact rolling synopsis. The synopsis lets the assistant "
    "keep the thread without re-reading every earlier turn. Preserve the KEY FACTS: "
    "what part is being built, its parameters and dimensions, design decisions and "
    "constraints, and anything the user explicitly asked for or rejected. Drop "
    "pleasantries and verbose tool chatter. Write it as a tight factual brief in a "
    "few sentences or bullet points — it is context, not a transcript."
)

# Prefix that frames the synthesised synopsis message so the model reads it as
# background context rather than a literal user instruction.
_SYNOPSIS_PREFIX = "[Earlier conversation summary]\n"


def needs_compaction(history: list[Message], *, config: Settings | None = None) -> bool:
    """True when ``history`` exceeds the configured compaction threshold.

    Pure policy predicate (no I/O): the transcript is compacted only when its
    message count is strictly greater than ``transcript_compact_threshold``. At or
    below the threshold the history is short enough to replay verbatim.
    """
    cfg = config or settings
    return len(history) > cfg.transcript_compact_threshold


def _message_text(message: Message) -> str:
    """Flatten a neutral message's blocks into plain text for summarisation."""
    parts: list[str] = []
    for block in message.content:
        if block.text:
            parts.append(block.text)
        elif block.kind == "tool_use" and block.name:
            parts.append(f"[tool {block.name} {block.input or {}}]")
        elif block.kind == "tool_result" and block.content:
            parts.append(f"[tool result {block.content}]")
    return " ".join(parts).strip()


def render_transcript(messages: list[Message]) -> str:
    """Render messages as a readable ``role: text`` transcript for the summariser."""
    lines: list[str] = []
    for message in messages:
        text = _message_text(message)
        if text:
            lines.append(f"{message.role}: {text}")
    return "\n".join(lines)


def summarize_messages(
    messages: list[Message],
    provider: ChatProvider,
    *,
    model: str | None = None,
    config: Settings | None = None,
) -> str:
    """Summarise ``messages`` into a rolling synopsis via the provider seam.

    Uses the one-shot :meth:`ChatProvider.complete` helper with the key-fact
    preserving :data:`_SUMMARY_SYSTEM` prompt. The model defaults to the configured
    orchestrator slug (resolved to a provider id). Injectable provider => unit
    testable offline with the fake.
    """
    cfg = config or settings
    model_id = model or resolve_model_id(cfg.orchestrator_model)
    user = (
        "Summarise the earlier conversation below into a rolling synopsis.\n\n"
        + render_transcript(messages)
    )
    return provider.complete(model=model_id, system=_SUMMARY_SYSTEM, user=user).strip()


async def compact_history(
    history: list[Message],
    provider: ChatProvider,
    *,
    config: Settings | None = None,
) -> list[Message]:
    """Return a bounded transcript: a rolling synopsis + the recent verbatim tail.

    Policy:

    * If ``history`` does not exceed ``transcript_compact_threshold``, it is
      returned **unchanged** (purely additive — no LLM call, no summarisation).
    * Otherwise the OLDER messages (everything before the last
      ``transcript_keep_recent``) are folded into a single synopsis ``Message`` via
      :func:`summarize_messages`, and the result is ``[synopsis, *recent_tail]`` —
      bounded to ``1 + transcript_keep_recent`` messages regardless of session
      length.

    The input list is never mutated and the persisted ``script_versions`` chain is
    untouched (this function has no store handle). Best-effort: if summarisation
    raises, we fall back to a **safe truncation** — the recent verbatim tail only —
    rather than crashing the turn.
    """
    cfg = config or settings
    if not needs_compaction(history, config=cfg):
        return history

    keep = max(cfg.transcript_keep_recent, 0)
    recent = history[-keep:] if keep else []
    older = history[: len(history) - keep]
    if not older:
        return recent

    try:
        synopsis_text = summarize_messages(older, provider, config=cfg)
    except Exception:  # best-effort: never crash the turn on a summariser failure
        logger.warning("transcript summarisation failed; falling back to truncation", exc_info=True)
        return recent

    if not synopsis_text:
        return recent

    synopsis = Message(
        role="user",
        content=[ContentBlock.of_text(_SYNOPSIS_PREFIX + synopsis_text)],
    )
    return [synopsis, *recent]
