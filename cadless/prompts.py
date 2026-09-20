"""Prompt assembly + code extraction.

Turns a natural-language intent into an LLM request (system prompt + few-shot
+ the request) routed through the provider seam's single-shot ``complete()``
, and extracts the build123d code from the model's reply. Also builds
the *repair* message used by the pipeline's repair loop.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from difflib import SequenceMatcher
from typing import TYPE_CHECKING

from cadless.api_subset import RESULT_VARIABLE
from cadless.config import settings
from cadless.few_shot import render_few_shot
from cadless.llm.provider import ChatProvider, ImagesUnsupported
from cadless.llm.registry import build_provider
from cadless.llm.types import (
    ContentBlock,
    Message,
    StreamEvent,
    TurnParams,
)
from cadless.params import extract_params
from cadless.printer_profile import AssemblySpec, fmt
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


#: Asked for only on a turn that carries a picture, so a text-only codegen prompt
#: is byte-for-byte what it always was.
#:
#: The marker is a line-leading label rather than a fence, because ``extract_code``
#: takes the first ```python block and a second fence would be a coin toss. The
#: reading is what a later turn is given in place of the pixels, which is why it
#: asks for proportions rather than adjectives.
REFERENCE_IMAGE_INSTRUCTION = (
    "The user attached a reference image. Build the shape it shows, applying any "
    "change they asked for in words.\n"
    "Before the code, write one paragraph beginning `REFERENCE:` describing what "
    "the picture shows — the shape, its main features, and their rough proportions "
    "— in enough detail that a later request could be answered from that paragraph "
    "alone, without the picture. Then write the code as usual."
)

# Ends at a blank line, at a fence, or at the end of the reply — all three matter.
# The fence terminator is not defensive: every few-shot exemplar is rendered as
# "Response:" then a fence on the next line with no blank line between them, so the
# model is conditioned into exactly the shape that would otherwise make the reading
# swallow the whole script. It would fail silently when it did — the code still
# extracts and the build still succeeds — and the flattened program would land in
# the stored reading, in every later turn's replay, in the synopsis, and in the
# alt text of the picture.
_READING = re.compile(
    r"^REFERENCE:[ \t]*(.*?)(?:\n[ \t]*\n|\n[ \t]*```|\Z)", re.MULTILINE | re.DOTALL
)


def extract_reading(text: str) -> str | None:
    """Pull the model's written reading of the reference image out of a reply.

    Returns ``None`` when the reply carries none — a model that ignored the
    instruction still produced code, and losing the cache is not a reason to fail
    the build.
    """
    match = _READING.search(text)
    if not match:
        return None
    reading = " ".join(match.group(1).split())
    return reading or None


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


def build_refinement_message(
    intent: str, prior_code: str, assembly: AssemblySpec | None = None
) -> str:
    """Message that asks the model to edit an existing script to satisfy a change.

    ``intent`` is the change request (the delta), e.g. "make the hole 8 mm".

    The instructions enforce a *surgical*, geometry-preserving edit: a
    small request must produce a small diff, not a from-scratch rewrite. The model
    is told to keep every other line of the existing script byte-for-byte identical
    and to change only what the request strictly requires — the earlier "smallest
    change" phrasing let the model replace a detailed multi-storey house with an
    over-simplified one on a small position tweak.

    ``assembly`` decides what the closing line asks ``result`` to be, exactly as it
    does in :func:`build_repair_message`. Editing an assembly and being told to
    finish with "the final solid" is an instruction to fuse it, which is the one
    thing the turn asked against -- and the prompt is otherwise silent on the
    subject, so nothing else in the message would disagree.
    """
    keeps = (
        "still assign the final solid to `result`"
        if assembly is None
        else (
            "still assign the whole assembly to `result` as a Compound of separate "
            "solids, with its interlocking joints and their clearance intact"
        )
    )
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
        f"targeted edit applied — and {keeps}. The "
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
    assembly: AssemblySpec | None = None,
) -> str:
    """Message that asks the model to fix code that failed to validate/execute.

    When a structured :class:`~cadless.worker.RepairContext` is supplied
    (execution failures), the prompt is line-anchored: it names the
    exception type, the offending source line, and the full traceback so the
    model can target deep OCCT failures precisely. Otherwise it falls back to the
    flat ``error`` string (e.g. validation/critique failures).

    ``assembly`` decides what the closing line asks ``result`` to be. It used to
    say "the final solid" unconditionally, which on an assembly turn instructed
    the model to undo the very thing that turn asked for -- and a repair round is
    where that instruction lands hardest, because the request it is repairing is
    the one hardest to get right in the first place.

    It settles the closing line and nothing else: the requirements themselves are
    framed around this message by the caller, the way every prompt in this module
    is framed. Applying them here as well would give the one builder that takes a
    spec two jobs the others do not have, and a caller that then framed it like
    its siblings -- the obvious change to make -- would send the rules twice.
    """
    failure = _format_failure(error, context)
    keeps = (
        "assigns the final solid to `result`"
        if assembly is None
        else (
            "assigns the whole assembly to `result` as a Compound of separate "
            "solids, with its interlocking joints and their clearance intact"
        )
    )
    message = (
        f"The following build123d script was generated for this request:\n"
        f"Request: {intent}\n\n"
        f"```python\n{previous_code.strip()}\n```\n\n"
        f"It failed with this error:\n{failure}\n\n"
        f"Return a corrected full script (code only) that fixes the error and still "
        f"{keeps}."
    )
    return message


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


def _with_reference_instruction(user: str, images: Sequence[ContentBlock]) -> str:
    """Prefix the reading instruction, but only when there is a picture to read.

    A turn with no attachment gets the message exactly as the builders produced
    it, which is what keeps the codegen contract unchanged for every existing
    caller — the eval harness and distillation included.
    """
    if not images:
        return user
    return f"{REFERENCE_IMAGE_INSTRUCTION}\n\n{user}"


def _fits(spec: AssemblySpec) -> str:
    """The build volume as every rule states it.

    One source because two builders interpolate it: rewording it in one and not the
    other would leave a fresh build and an edit describing different printers, and
    neither string would look wrong on its own.
    """
    volume = spec.volume
    return f"{fmt(volume.width)} x {fmt(volume.depth)} x {fmt(volume.height)} mm"


def _assembly_rules(spec: AssemblySpec) -> str:
    """What an assembly turn asks for, written against the printer it is for.

    The measurements are interpolated rather than described because the model has
    to size parts against numbers: "make it fit your printer" is not something a
    generator can act on.

    This is the whole brief -- the constraints AND the design decisions -- so it
    belongs to a round entitled to decide the split: a fresh generation and every
    repair beneath one, plus any repair the assembly check forced. An edit is not
    entitled to, nor is a repair beneath an edit, and both take
    :func:`_assembly_edit_rules` instead.
    """
    fits = _fits(spec)
    return (
        f"This part is for a 3D printer whose build volume is {fits}, and it is "
        "too large to print in one piece. Build it as an ASSEMBLY of separate "
        "solids:\n"
        f"  * Split it into the FEWEST parts such that each one fits within {fits} "
        "on its own.\n"
        "  * Put each seam where a cut does least harm: at a natural boundary, not "
        "through a feature and not across a face meant to be seen.\n"
        "  * Join the parts with an interlocking dovetail or jigsaw profile cut "
        "into the mating faces, so the assembly holds without glue. A plain flat "
        "butt face is NOT acceptable.\n"
        f"  * Leave {fmt(spec.clearance_mm)} mm of clearance on every mating face "
        "-- cut the socket that much larger than the tab it receives. A joint that "
        "is exact in CAD does not go together in plastic.\n"
        "  * Orient every seam so the interlocking faces print without support: "
        "sweep each joint profile along an axis lying in the build plane, never "
        "overhanging it.\n"
        f"  * Assign the whole assembly to `{RESULT_VARIABLE}` as a Compound of the "
        "separate solids. Do NOT fuse the parts into one connected solid."
    )


def _assembly_edit_rules(spec: AssemblySpec) -> str:
    """The constraints a round that may not decide the split must respect, without
    the brief for choosing one.

    An edit acts on a model whose split already exists, so asking it again for the
    fewest parts and where the seams go argues with the same message's "EDIT in
    place, not redesign" and invites the rewrite that message exists to prevent.
    A repair beneath an edit takes these rules for the same reason -- it is that
    edit one round on, fixing what the edit produced. A repair forced by the
    assembly check does not, because there the split is what failed. What any such
    round must still respect is the arithmetic it cannot infer: the bed each solid
    has to fit, and the gap a joint needs to go together in plastic.

    The Compound requirement is deliberately absent. Both builders these rules are
    wrapped around -- :func:`build_refinement_message` and
    :func:`build_repair_message` -- already close an assembly turn by asking for
    it, so restating it here would be the same instruction arriving twice.

    Two things this must not do. It must not assert that the script in front of the
    model already is an assembly: a spec does not prove the current script is one,
    because asking on the turn is enough to produce it, so "ask for an assembly,
    then edit a single-solid model" reaches here and would be told something false
    about its own input. And having
    left the seam question open, it cannot then leave a seam the request *does* ask
    for unspecified -- nothing downstream measures joint shape or print orientation,
    so a butt-jointed or unprintable new seam would pass every check there is.
    """
    fits = _fits(spec)
    return (
        f"This turn is for a 3D printer whose build volume is {fits}. Every separate "
        f"solid in the result must fit within {fits} on its own, and every mating "
        f"face must keep {fmt(spec.clearance_mm)} mm of clearance -- a joint that is "
        "exact in CAD does not go together in plastic. Do not add, remove or move a "
        "seam unless the change request asks for it; where it does, cut that seam as "
        "an interlocking dovetail or jigsaw profile rather than a flat butt face, and "
        "orient it to print without support."
    )


def _with_assembly_instruction(user: str, spec: AssemblySpec | None) -> str:
    """Prefix the assembly requirements, but only when this turn asked for them.

    Shaped exactly like :func:`_with_reference_instruction` above and for the same
    reason: a turn that did not ask gets the message the builders produced, byte
    for byte, so every existing caller keeps the prompt it has always sent
    without having to know this exists.
    """
    if spec is None:
        return user
    return f"{_assembly_rules(spec)}\n\n{user}"


def _with_assembly_edit_instruction(user: str, spec: AssemblySpec | None) -> str:
    """Prefix the edit-path constraints. Shaped exactly like its sibling above.

    A second named wrapper rather than a flag on the first: the caller already
    knows whether it is generating or editing, so naming the wrapper keeps that
    decision where it is made, while a flag would carry the question to every call
    site and read as configuration rather than as which round this is.
    """
    if spec is None:
        return user
    return f"{_assembly_edit_rules(spec)}\n\n{user}"


def _emit_reading(
    text: str, images: Sequence[ContentBlock], on_reading: Callable[[str], None] | None
) -> None:
    """Hand the model's reading to ``on_reading``, if it wrote one and anyone asked.

    Silent on every other path. A missing reading is not an error: the code is the
    deliverable and the reading is what saves a later turn from paying for the
    pixels again.
    """
    if not images or on_reading is None:
        return
    reading = extract_reading(text)
    if reading:
        on_reading(reading)


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
        images: Sequence[ContentBlock] = (),
        on_reading: Callable[[str], None] | None = None,
        assembly: AssemblySpec | None = None,
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

        ``images`` are the turn's reference pictures. They force the message path
        whatever ``on_token`` is, because ``complete()`` takes a bare string; an
        empty sequence (the default) leaves the routing above exactly as it was.

        ``assembly`` is the printer this turn is building for, present only when
        the turn asked for a part-wise model. ``None`` (the default) is what every
        existing caller passes without knowing it, and produces the prompt they
        have always sent.
        """
        user = _with_assembly_instruction(
            _with_reference_instruction(build_user_message(intent, grounding), images),
            assembly,
        )
        if on_token is None and not images:
            text = self._provider.complete(
                model=self._model,
                system=SYSTEM_PROMPT,
                user=user,
                temperature=temperature,
            )
        else:
            text = self._stream_complete(user, temperature, on_token, images)
        _emit_reading(text, images, on_reading)
        return extract_code(text)

    def _stream_complete(
        self,
        user: str,
        temperature: float | None,
        on_token: Callable[[str], None] | None,
        images: Sequence[ContentBlock] = (),
    ) -> str:
        """Mirror ``complete()`` over the message path, optionally streaming.

        This is the only shape on the seam that can carry anything but a string,
        so it is where an image has to go. ``on_token`` is optional here (unlike
        in ``generate``): a call routed through this path only because it carries
        a picture still has no listener for the deltas.

        The pictures go **before** the words. ``user`` ends on ``Response:``, the
        model's cue to start writing, so anything appended after it lands between
        the cue and the answer.
        """
        if images and not self._provider.capabilities(self._model).supports_images:
            # The backstop the request boundary makes unnecessary — for the callers
            # that are not it. Eval, distillation and anything composed beside the
            # engine reach this directly, and without the check the picture goes to
            # the vendor and comes back as whatever that API calls a malformed
            # request. Refuse in the seam's own vocabulary instead.
            # Named off the provider in hand, not the configured one: this generator
            # may have been given a provider directly, and reporting the setting
            # would name something that was never called.
            raise ImagesUnsupported(
                getattr(self._provider, "PROVIDER_NAME", type(self._provider).__name__)
            )

        parts: list[str] = []
        content = [*images, ContentBlock.of_text(user)]
        for chunk in self._provider.stream_turn(
            model=self._model,
            system=SYSTEM_PROMPT,
            messages=[Message(role="user", content=content)],
            tools=[],
            params=TurnParams(temperature=temperature),
        ):
            if chunk.event == StreamEvent.TEXT_DELTA:
                token = chunk.payload.get("text", "")
                if token:
                    parts.append(token)
                    if on_token is not None:
                        on_token(token)
        return "".join(parts)

    def refine(
        self,
        intent: str,
        prior_code: str,
        images: Sequence[ContentBlock] = (),
        on_reading: Callable[[str], None] | None = None,
        assembly: AssemblySpec | None = None,
    ) -> str:
        """Edit existing code to satisfy a change request (the delta ``intent``).

        ``images`` route the call through the message path — without them it stays
        on the one-shot ``complete()`` exactly as before.

        ``assembly`` is here for the same reason it is on ``generate``: an edit to
        an assembly is still an assembly, and a round that lost the spec would be
        editing against the default closing rule, which asks for one connected
        solid. The wrapping order matches ``generate`` exactly; what differs is
        which rules go in. An edit is the one round not entitled to choose the
        split, so it is framed by the constraints alone -- see
        :func:`_assembly_edit_rules`.
        """
        user = _with_assembly_edit_instruction(
            _with_reference_instruction(
                build_refinement_message(intent, prior_code, assembly), images
            ),
            assembly,
        )
        if images:
            text = self._stream_complete(user, None, None, images)
        else:
            text = self._provider.complete(
                model=self._model,
                system=SYSTEM_PROMPT,
                user=user,
            )
        _emit_reading(text, images, on_reading)
        return extract_code(text)

    def repair(
        self,
        intent: str,
        previous_code: str,
        error: str,
        context: RepairContext | None = None,
        images: Sequence[ContentBlock] = (),
        assembly: AssemblySpec | None = None,
        *,
        may_resplit: bool = True,
    ) -> str:
        """Fix code that failed, with the turn's reference pictures still in view.

        A repair round that lost the picture would be trying to fix the shape
        against the words alone, which is the half of the request that was least
        able to describe it in the first place. ``assembly`` is here for the same
        reason: a round that lost it would be repairing towards a single solid.

        Both are framed in the same place and the same order as ``generate`` and
        ``refine`` frame them. Handing the blocks over is not the same as asking
        for them to be read, and this path used to do only the first -- the
        picture arrived with nothing saying what to do with it.

        ``may_resplit`` chooses between the two assembly framings, and the
        question it asks is entitlement rather than which round this is: a repair
        beneath an edit is not entitled to redesign the split, *except* where the
        assembly check is itself what failed, which is the one failure a redesign
        answers among the stages wired to carry a spec today. Only the caller
        knows which stage produced the error, so only
        the caller can answer it. The default is the framing every caller had
        before this parameter existed, so a caller that does not know keeps the
        prompt it has always sent -- a caller that does know is expected to say
        so rather than lean on it.
        """
        frame = _with_assembly_instruction if may_resplit else _with_assembly_edit_instruction
        user = frame(
            _with_reference_instruction(
                build_repair_message(intent, previous_code, error, context, assembly), images
            ),
            assembly,
        )
        if images:
            text = self._stream_complete(user, None, None, images)
        else:
            text = self._provider.complete(
                model=self._model,
                system=SYSTEM_PROMPT,
                user=user,
            )
        return extract_code(text)
