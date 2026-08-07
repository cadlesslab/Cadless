"""Provider-neutral LLM domain types.

These types are the lingua franca between the app and any chat provider. They
are deliberately **vendor-free** — no boto3, no anthropic imports — so the same
``Message``/``ContentBlock`` graph can be translated to Bedrock Converse, the
Anthropic Messages API, or an in-memory fake.

Every :class:`ContentBlock` may carry an optional ``provider`` tag plus an opaque
``provider_raw`` payload: the verbatim block as the provider emitted it. That lets
a provider replay assistant turns (e.g. signed ``thinking`` blocks, tool_use
blocks) back to the same vendor without lossy re-encoding.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

# Roles in a chat transcript.
Role = Literal["user", "assistant"]

# Discriminated-union tag for a content block. ``clarification`` is a
# neutral, vendor-free block: it persists the assistant's quick-reply questions so
# a reload can restore the chips. It is never replayed to a provider as a tool
# block — it is a terminal UI artifact of a turn that ended awaiting the user.
BlockKind = Literal["text", "thinking", "tool_use", "tool_result", "clarification", "plan"]


class ContentBlock(BaseModel):
    """One block inside a :class:`Message`.

    ``kind`` discriminates the union. Fields not relevant to a kind stay ``None``.
    Use the ``text``/``thinking``/``tool_use``/``tool_result`` classmethods rather
    than the raw constructor for clarity.
    """

    kind: BlockKind

    # text / thinking
    text: str | None = None

    # tool_use
    id: str | None = None
    name: str | None = None
    input: dict[str, Any] | None = None

    # tool_result
    tool_use_id: str | None = None
    content: str | None = None
    is_error: bool = False

    # Provenance: which provider emitted this block, plus the verbatim block for
    # lossless replay back to that provider.
    provider: str | None = None
    provider_raw: dict[str, Any] | None = None

    # Constructors are prefixed ``of_`` so they don't shadow the like-named fields
    # (pydantic would otherwise drop the field, e.g. a ``text`` classmethod hides
    # the ``text`` attribute).
    @classmethod
    def of_text(cls, text: str, **kw: Any) -> ContentBlock:
        return cls(kind="text", text=text, **kw)

    @classmethod
    def of_thinking(cls, text: str, **kw: Any) -> ContentBlock:
        return cls(kind="thinking", text=text, **kw)

    @classmethod
    def of_tool_use(cls, *, id: str, name: str, input: dict[str, Any], **kw: Any) -> ContentBlock:
        return cls(kind="tool_use", id=id, name=name, input=input, **kw)

    @classmethod
    def of_clarification(cls, *, questions: list[dict[str, Any]], **kw: Any) -> ContentBlock:
        """A neutral clarification block carrying quick-reply ``questions``.

        Each question is ``{text, options?[]}``. The questions live under ``input``
        (reusing the tool_use payload field) so the persisted ``blocks_json`` and
        the frontend ``ContentBlock`` mapping restore them on reload.
        """
        return cls(kind="clarification", input={"questions": questions}, **kw)

    @classmethod
    def of_plan(cls, *, steps: list[str], **kw: Any) -> ContentBlock:
        """A neutral plan block carrying ordered ``steps``.

        For non-trivial parts the model emits a short ordered plan before acting.
        The steps live under ``input`` (reusing the tool_use payload field) so the
        persisted ``blocks_json`` and the frontend ``ContentBlock`` mapping restore
        them on reload. Like ``clarification`` it is a terminal UI artifact — never
        replayed to a provider as a tool block.
        """
        return cls(kind="plan", input={"steps": steps}, **kw)

    @classmethod
    def of_tool_result(
        cls, *, tool_use_id: str, content: str, is_error: bool = False, **kw: Any
    ) -> ContentBlock:
        return cls(
            kind="tool_result",
            tool_use_id=tool_use_id,
            content=content,
            is_error=is_error,
            **kw,
        )


class Message(BaseModel):
    """A single turn: a role plus its ordered content blocks."""

    role: Role
    content: list[ContentBlock]


class ToolDef(BaseModel):
    """A provider-neutral tool definition (JSON-Schema input)."""

    name: str
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)


class StreamEvent(StrEnum):
    """Events emitted while streaming a turn.

    Providers translate their native streaming protocol into this neutral
    sequence. Event *payloads* (deltas, ids, stop reasons, usage) are carried out
    of band by the provider's iterator wrapper in later issues; this enum names
    the event boundaries the app reacts to.
    """

    TURN_START = "turn_start"
    TEXT_DELTA = "text_delta"
    THINKING_DELTA = "thinking_delta"  # carries {text}
    THINKING_STOP = "thinking_stop"  # carries {text, block} — the verbatim block
    TOOL_USE_START = "tool_use_start"  # carries {name, id}
    TOOL_INPUT_DELTA = "tool_input_delta"  # carries {partial_json}
    TOOL_USE_STOP = "tool_use_stop"
    TURN_DELTA = "turn_delta"  # carries {stop_reason}
    USAGE = "usage"
    TURN_STOP = "turn_stop"


class StopReason(StrEnum):
    """Why a turn stopped, normalized across providers."""

    END_TURN = "end_turn"
    TOOL_USE = "tool_use"
    MAX_TOKENS = "max_tokens"
    STOP_SEQUENCE = "stop_sequence"


class Usage(BaseModel):
    """Token accounting for a turn."""

    input_tokens: int = 0
    output_tokens: int = 0


class Capabilities(BaseModel):
    """What a given model supports, so the app can adapt requests.

    Reports at least: extended-thinking support, whether ``tool_choice`` can be
    constrained, and the model's max output-token budget.
    """

    supports_thinking: bool = False
    supports_tool_choice: bool = False
    max_output_tokens: int = 4096


class TurnParams(BaseModel):
    """Per-turn generation knobs, provider-neutral.

    ``None`` means "use the provider/model default".
    """

    max_tokens: int | None = None
    temperature: float | None = None
    thinking: bool = False
    thinking_budget_tokens: int | None = None
    tool_choice: Literal["auto", "any", "none"] | None = None
    stop_sequences: list[str] | None = None
