"""Optional VLM render-critique repair signal — OFF by default.

After a model executes successfully, render the part from several sides and ask
a vision model whether the shape matches the request. A "mismatch" verdict
becomes an extra repair signal in the pipeline: error-only repair catches code
that *crashes*, and this catches code that *runs but builds the wrong shape*.

One view is not enough to ask the question. An isometric render shows three
faces and hides the rest, so a missing back cut-out or a far-side hole reads as
a match — precisely the failures worth catching. The critic therefore asks for
a set, and how many is a setting rather than a constant, because every view is
another image on every turn.

The renderer stays **injected**. This module decides what to ask and how to read
the answer; it knows nothing about how a mesh becomes an image, and without a
renderer supplied it does nothing at all.
"""

from __future__ import annotations

import base64
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from cadless.catalog.thumbnail import VIEW_ORDER
from cadless.config import Settings, settings
from cadless.llm.provider import ChatProvider, ImagesUnsupported
from cadless.llm.registry import build_provider
from cadless.llm.types import ContentBlock, Message, StreamEvent, TurnParams

# A renderer maps (mesh path, view names) to one PNG per view, in that order.
Renderer = Callable[[str, Sequence[str]], list[tuple[str, bytes]]]

_MEDIA_TYPE = "image/png"
_MAX_TOKENS = 300

_SYSTEM = (
    "You are checking whether a CAD part matches what was asked for. "
    "You are shown the same part rendered from several sides at one scale. "
    "Judge the shape, not the rendering: colour, lighting and image quality "
    "are not part of the question."
)


@dataclass
class Critique:
    matches: bool
    feedback: str
    #: The ``(view, PNG bytes)`` the verdict was formed from. Carried out
    #: because what the reviewer saw is shown to the user beside what it said —
    #: a verdict with no picture asks the reader to take it on trust.
    captures: list[tuple[str, bytes]] = field(default_factory=list)


def _question(intent: str, views: Sequence[str]) -> str:
    return (
        f"These are renders of one CAD part, viewed from {', '.join(views)}, "
        f"generated for the request:\n"
        f'"{intent}"\n\n'
        f"Does the geometry match the request? Reply with exactly 'MATCH' if it "
        f"does, or 'MISMATCH: <what is wrong>' if it does not."
    )


class VlmCritic:
    """Asks a vision model whether a built solid is the shape that was asked for."""

    def __init__(
        self,
        renderer: Renderer,
        provider: ChatProvider | None = None,
        config: Settings | None = None,
    ):
        self._render = renderer
        self._cfg = config or settings
        self._provider = provider

    @property
    def provider(self) -> ChatProvider:
        """The configured provider, built on first use.

        Lazy so that constructing a critic cannot fail where no credential is
        resolvable — the wiring point builds one for every turn, including the
        turns that never reach a critique.
        """
        if self._provider is None:
            self._provider = build_provider(settings=self._cfg)
        return self._provider

    @property
    def views(self) -> tuple[str, ...]:
        """The views one critique carries, clamped to what the renderer names."""
        count = int(self._cfg.vlm_critique_view_count)
        return VIEW_ORDER[: max(1, min(count, len(VIEW_ORDER)))]

    def critique(self, intent: str, mesh_path: str) -> Critique:
        provider = self.provider
        # The slug, not a resolved vendor id: every adapter resolves the slug
        # itself, and handing one a resolved id raises on every call. The
        # pipeline reads a critique failure as a repair signal, so that mistake
        # would take the rung out quietly instead of loudly.
        model = self._cfg.vlm_model_slug
        if not provider.capabilities(model).supports_images:
            # Checked before rendering: a blind model makes the pictures wasted
            # work, and refusing in the seam's own vocabulary says which layer
            # said no. Named off the provider in hand rather than the configured
            # one, which may not be what this critic was given.
            raise ImagesUnsupported(getattr(provider, "PROVIDER_NAME", type(provider).__name__))

        shots = self._render(mesh_path, self.views)
        content = [
            ContentBlock.of_image(
                data=base64.standard_b64encode(png).decode("ascii"),
                media_type=_MEDIA_TYPE,
                reading=f"{name} view of the generated part",
            )
            for name, png in shots
        ]
        # Pictures before words: the request ends on the question, so anything
        # appended after it lands between the cue and the answer.
        content.append(ContentBlock.of_text(_question(intent, [name for name, _ in shots])))

        parts: list[str] = []
        for chunk in provider.stream_turn(
            model=model,
            system=_SYSTEM,
            messages=[Message(role="user", content=content)],
            tools=[],
            params=TurnParams(max_tokens=_MAX_TOKENS, temperature=0.0),
        ):
            if chunk.event == StreamEvent.TEXT_DELTA:
                parts.append(chunk.payload.get("text", ""))

        verdict = parse_verdict("".join(parts))
        verdict.captures = shots
        return verdict


def parse_verdict(text: str) -> Critique:
    stripped = text.strip()
    if stripped.upper().startswith("MATCH"):
        return Critique(matches=True, feedback="")
    feedback = stripped
    if ":" in stripped:
        feedback = stripped.split(":", 1)[1].strip()
    return Critique(matches=False, feedback=feedback or "model does not match the request")
