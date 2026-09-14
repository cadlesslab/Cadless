"""Putting the assembly into words -- on, wherever a writer is injected.

The engine already knows how a multi-part build goes together: the order search
established a sequence in which every part reaches its place, and the mating
check established which parts meet which. What it does not know is what to call
them. ``base plate`` and ``left arm`` are in the request and in the code the
model wrote, and nowhere in the geometry.

So the work is split, and the split is the point. **The sentences are built
here, from the measured order and joints. The model supplies only the names.**
Asked for the steps themselves a model would sometimes state a different order,
and a guide that contradicts the order the engine proved collision-free is worse
than one with dull names: a reader can follow dull names, and cannot follow a
sequence that does not go together. There is nowhere in this module for a model
to put an order, so it cannot give a wrong one.

Losing the model therefore costs a guide its vocabulary and never its sequence.
The parts are built and checked by the time this runs, so nothing here raises:
the worst outcome is the guide the engine could write unaided.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass

from cadless.config import Settings, settings
from cadless.llm.provider import ChatProvider
from cadless.llm.registry import build_provider
from cadless.llm.types import ContentBlock, Message, StreamEvent, TurnParams

logger = logging.getLogger(__name__)

_MAX_TOKENS = 512

_SYSTEM = (
    "You name the parts of a 3D-printed assembly. "
    "You are given the script that built it and how many parts it was split "
    "into. Reply with names only: the order the parts go together in is already "
    "settled and is not yours to give."
)

#: The first ``{`` to the last ``}``. A model asked for JSON returns it inside a
#: fence, after a sentence of preamble, or on its own, and all three are ordinary
#: rather than failures worth losing the names over.
_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


@dataclass
class AssemblyGuide:
    """How a multi-part build goes together, as a reader is shown it."""

    #: One name per part, in part order.
    parts: list[str]
    #: One sentence per assembly step, in the order the parts go together.
    steps: list[str]

    def as_payload(self) -> dict:
        """The shape this travels in, which is JSON and must stay JSON."""
        return {"parts": list(self.parts), "steps": list(self.steps)}


def plain_guide(order: Sequence[int], joints: Sequence[Sequence[int]]) -> AssemblyGuide:
    """The guide the engine writes unaided, naming parts as it numbers them."""
    names = [f"part {index + 1}" for index in range(len(order))]
    return AssemblyGuide(parts=names, steps=build_steps(order, joints, names))


def build_steps(
    order: Sequence[int], joints: Sequence[Sequence[int]], names: Sequence[str]
) -> list[str]:
    """One sentence per step, from the measured order and joints."""
    steps: list[str] = []
    placed: list[int] = []
    for position, index in enumerate(order):
        if position == 0:
            steps.append(f"Start with {names[index]}.")
            placed.append(index)
            continue
        # Only what is already on the bench. A part's other neighbours are still
        # in the box, and naming one there tells a reader to fit something they
        # have not been handed yet.
        neighbours = [
            other
            for other in (joints[index] if index < len(joints) else [])
            if other in placed and other < len(names)
        ]
        if neighbours:
            joined = ", ".join(names[other] for other in neighbours)
            steps.append(f"Fit {names[index]} to {joined}.")
        else:
            steps.append(f"Add {names[index]}.")
        placed.append(index)
    return steps


class GuideWriter:
    """Asks a model what the parts of an assembly should be called."""

    def __init__(self, provider: ChatProvider | None = None, config: Settings | None = None):
        self._cfg = config or settings
        self._provider = provider

    @property
    def provider(self) -> ChatProvider:
        """The configured provider, built on first use.

        Lazy so that constructing a writer cannot fail where no credential is
        resolvable: a turn that never asks for an assembly never reaches here.
        """
        if self._provider is None:
            self._provider = build_provider(settings=self._cfg)
        return self._provider

    def write(
        self,
        intent: str,
        code: str,
        order: Sequence[int],
        joints: Sequence[Sequence[int]],
    ) -> AssemblyGuide:
        """The guide for this build. Never raises; falls back to plain names."""
        names = self._names(intent, code, len(order))
        if names is None:
            return plain_guide(order, joints)
        return AssemblyGuide(parts=names, steps=build_steps(order, joints, names))

    def _names(self, intent: str, code: str, count: int) -> list[str] | None:
        try:
            reply = self._ask(intent, code, count)
        except Exception:  # noqa: BLE001 - a guide is worth less than the build it describes
            # Logged rather than passed over. Everything a model call can raise
            # is caught here, which during this module's own development quietly
            # swallowed a wrong attribute name and left every guide looking as
            # though the model had simply declined. A fallback that hides its
            # reason is indistinguishable from one that is never reached.
            logger.warning("assembly guide: naming the parts failed", exc_info=True)
            return None
        return _read_names(reply, count)

    def _ask(self, intent: str, code: str, count: int) -> str:
        provider = self.provider
        chunks: list[str] = []
        for chunk in provider.stream_turn(
            # The fast model: naming a handful of parts from a script in front of
            # it is a short, cheap task, and a guide is not worth the primary
            # model's price on every assembly turn.
            model=self._cfg.bedrock_fast_model_slug,
            system=_SYSTEM,
            messages=[
                Message(role="user", content=[ContentBlock.of_text(_question(intent, code, count))])
            ],
            tools=[],
            params=TurnParams(max_tokens=_MAX_TOKENS, temperature=0.0),
        ):
            if chunk.event == StreamEvent.TEXT_DELTA:
                chunks.append(chunk.payload.get("text", ""))
        return "".join(chunks)


def _question(intent: str, code: str, count: int) -> str:
    return (
        f"This script builds an assembly of {count} separate parts, printed "
        f"separately and put together by hand. It was written for the request:\n"
        f'"{intent}"\n\n'
        f"```python\n{code}\n```\n\n"
        f"Name each part as someone holding it would: what it is, not where it "
        f"sits in the list. Reply with JSON only, an object with one key "
        f'"parts", holding exactly {count} short names in the order the script '
        f"builds them."
    )


def _read_names(reply: str, count: int) -> list[str] | None:
    """The names out of a reply, or ``None`` where there is nothing usable.

    Everything is checked before anything is used. A reply short by one name
    would otherwise be taken for the parts it does cover and leave the rest
    unnamed, which reads as a guide about a different assembly.
    """
    found = _OBJECT.search(reply or "")
    if found is None:
        return None
    try:
        data = json.loads(found.group(0))
    except ValueError:
        return None
    names = data.get("parts") if isinstance(data, dict) else None
    if not isinstance(names, list) or len(names) != count:
        return None
    if not all(isinstance(name, str) and name.strip() for name in names):
        return None
    return [name.strip() for name in names]
