"""Prompt assembly + code extraction.

Turns a natural-language intent into an LLM request (system prompt + few-shot
+ the request) routed through the provider seam's single-shot ``complete()``
, and extracts the build123d code from the model's reply. Also builds
the *repair* message used by the pipeline's repair loop.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from difflib import SequenceMatcher
from typing import TYPE_CHECKING

from cadless.config import settings
from cadless.few_shot import render_few_shot
from cadless.llm.provider import ChatProvider
from cadless.llm.registry import build_provider
from cadless.llm.types import (
    ContentBlock,
    Message,
    StreamEvent,
    TurnParams,
)
from cadless.params import extract_params
from cadless.system_prompt import SYSTEM_PROMPT

if TYPE_CHECKING:
    from cadless.worker import RepairContext

_FENCE = re.compile(r"```(?:python)?\s*\n?(.*?)```", re.DOTALL)


def extract_code(text: str) -> str:
    """Pull the Python out of a model reply.

    Prefers the first fenced ```python block; falls back to the whole reply with
    a leading 'Response:' label stripped.
    """
    match = _FENCE.search(text)
    code = match.group(1) if match else text
    code = re.sub(r"^\s*Response:\s*", "", code)
    return code.strip() + "\n"


def extract_code_and_params(text: str) -> tuple[str, dict]:
    """Extract the build123d code and its declared ``params`` dict from a reply.

    Convenience wrapper over :func:`extract_code` + :func:`extract_params`.
    """
    code = extract_code(text)
    return code, extract_params(code)


def build_user_message(intent: str, grounding: str | None = None) -> str:
    """Few-shot examples followed by the user's request.

    ``grounding`` is the optional dynamic-RAG block: known-good KB
    examples retrieved for this request and framed as suggestions to adapt. It is
    purely additive — when empty or ``None`` the returned message is byte-for-byte
    the legacy no-retrieval prompt. When present it is inserted between the few-shot
    examples and the request so the model sees it as extra grounding.
    """
    base = f"{render_few_shot()}\n\nRequest: {intent}\nResponse:"
    if not grounding:
        return base
    return f"{render_few_shot()}\n\n{grounding}\n\nRequest: {intent}\nResponse:"


def build_refinement_message(intent: str, prior_code: str) -> str:
    """Message that asks the model to edit an existing script to satisfy a change.

    ``intent`` is the change request (the delta), e.g. "make the hole 8 mm".

    The instructions enforce a *surgical*, geometry-preserving edit: a
    small request must produce a small diff, not a from-scratch rewrite. The model
    is told to keep every other line of the existing script byte-for-byte identical
    and to change only what the request strictly requires — the earlier "smallest
    change" phrasing let the model replace a detailed multi-storey house with an
    over-simplified one on a small position tweak.
    """
    return (
        f"Here is an existing build123d script. It is the source of truth — treat it "
        f"as code you must EDIT in place, not redesign:\n\n"
        f"```python\n{prior_code.strip()}\n```\n\n"
        f"Apply ONLY this change request:\n{intent}\n\n"
        f"Make a MINIMAL, surgical edit:\n"
        f"- Change only the lines strictly necessary to satisfy the request.\n"
        f"- Keep ALL other geometry, structure, parameters, and values exactly as "
        f"they are — copy every unrelated line through byte-for-byte unchanged.\n"
        f"- Do NOT rewrite the script from scratch, do NOT simplify, summarise, or "
        f"drop existing parts/features, and do NOT reduce the level of detail. "
        f"Preserve the existing complexity.\n"
        f"- For a pure dimensional change, prefer editing the value in the existing "
        f"`params` block and keep that block in sync with the geometry.\n\n"
        f"Return the FULL updated script (code only) — the original with just your "
        f"targeted edit applied — and still assign the final solid to `result`. The "
        f"output should differ from the input by a small diff."
    )


def refine_diff_ratio(before: str, after: str) -> float:
    """Fraction of code that changed between ``before`` and ``after`` (0.0–1.0).

    The edit-similarity metric the refine guardrail/eval keys on: a
    surgical edit (a tweaked line or two) scores near 0, while a from-scratch
    rewrite or over-simplification (the 9244 -> 802 char collapse the bug
    describes) scores near 1. Computed as ``1 - SequenceMatcher.ratio`` over the
    two sources, so it is a normalized, length-aware measure of how much of the
    script the edit churned. Pure and deterministic — trivially unit-testable and
    safe to assert on in evals.
    """
    if before == after:
        return 0.0
    return 1.0 - SequenceMatcher(None, before, after).ratio()


def build_repair_message(
    intent: str,
    previous_code: str,
    error: str,
    context: RepairContext | None = None,
) -> str:
    """Message that asks the model to fix code that failed to validate/execute.

    When a structured :class:`~cadless.worker.RepairContext` is supplied
    (execution failures), the prompt is line-anchored: it names the
    exception type, the offending source line, and the full traceback so the
    model can target deep OCCT failures precisely. Otherwise it falls back to the
    flat ``error`` string (e.g. validation/critique failures).
    """
    failure = _format_failure(error, context)
    return (
        f"The following build123d script was generated for this request:\n"
        f"Request: {intent}\n\n"
        f"```python\n{previous_code.strip()}\n```\n\n"
        f"It failed with this error:\n{failure}\n\n"
        f"Return a corrected full script (code only) that fixes the error and still "
        f"assigns the final solid to `result`."
    )


def _format_failure(error: str, context: RepairContext | None) -> str:
    """Render the failure block, preferring structured context when present."""
    if context is None:
        return error
    parts = [f"{context.error_type}: {context.message}".rstrip(": ")]
    if context.offending_line:
        parts.append(f"Offending line: {context.offending_line}")
    if context.last_traceback:
        parts.append(f"Traceback:\n{context.last_traceback.strip()}")
    return "\n".join(parts)


class CodeGenerator:
    """Generates build123d code from intent (and repairs it) via the LLM seam.

    Routes every call through a :class:`~cadless.llm.provider.ChatProvider`'s
    single-shot ``complete()`` — the one wire-format layer shared with the
    conversational agent. ``provider`` defaults to the configured provider
    (``CADLESS_LLM_PROVIDER``); ``model`` is a slug (defaults to the codegen model
    ``settings.bedrock_model_slug``) that the adapter resolves to a runtime ID.
    """

    def __init__(self, provider: ChatProvider | None = None, model: str | None = None):
        self._provider = provider or build_provider()
        self._model = model or settings.bedrock_model_slug

    def generate(
        self,
        intent: str,
        grounding: str | None = None,
        temperature: float | None = None,
        on_token: Callable[[str], None] | None = None,
    ) -> str:
        """Generate build123d code from ``intent``.

        ``grounding`` is the optional dynamic-RAG block of retrieved
        known-good examples; it is forwarded to :func:`build_user_message` and is
        purely additive (empty/None => the legacy no-retrieval prompt).

        ``temperature`` overrides the provider default for this call —
        the best-of-N fan-out raises it for candidate diversity. ``None`` (the
        default) keeps the configured single-run temperature unchanged.

        ``on_token`` makes the call stream: each text delta is passed to
        it as the model writes the code, so the chat layer can show the codegen
        live. ``None`` (the default) keeps the one-shot ``complete()`` path byte-
        for-byte — used by refine/repair and the non-chat callers (eval, distill).
        """
        user = build_user_message(intent, grounding)
        if on_token is None:
            text = self._provider.complete(
                model=self._model,
                system=SYSTEM_PROMPT,
                user=user,
                temperature=temperature,
            )
        else:
            text = self._stream_complete(user, temperature, on_token)
        return extract_code(text)

    def _stream_complete(
        self,
        user: str,
        temperature: float | None,
        on_token: Callable[[str], None],
    ) -> str:
        """Mirror ``complete()`` but surface each text delta through ``on_token``."""
        parts: list[str] = []
        for chunk in self._provider.stream_turn(
            model=self._model,
            system=SYSTEM_PROMPT,
            messages=[Message(role="user", content=[ContentBlock.of_text(user)])],
            tools=[],
            params=TurnParams(temperature=temperature),
        ):
            if chunk.event == StreamEvent.TEXT_DELTA:
                token = chunk.payload.get("text", "")
                if token:
                    parts.append(token)
                    on_token(token)
        return "".join(parts)

    def refine(self, intent: str, prior_code: str) -> str:
        """Edit existing code to satisfy a change request (the delta ``intent``)."""
        text = self._provider.complete(
            model=self._model,
            system=SYSTEM_PROMPT,
            user=build_refinement_message(intent, prior_code),
        )
        return extract_code(text)

    def repair(
        self,
        intent: str,
        previous_code: str,
        error: str,
        context: RepairContext | None = None,
    ) -> str:
        text = self._provider.complete(
            model=self._model,
            system=SYSTEM_PROMPT,
            user=build_repair_message(intent, previous_code, error, context),
        )
        return extract_code(text)
