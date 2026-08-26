"""Whether a model fits the machine, and what to offer when it does not.

Answered from the bounding box a version already carries, before any slicer
runs, so somebody hears "this model is 1600 x 900 x 750 mm and the printer's
build volume is 210 x 200 x 195 mm" immediately rather than waiting for a slice
to end in ``All objects are outside of the print volume``, which names neither
size.

Kept apart from :mod:`cadless.slicing` because nothing here runs anything. It is
arithmetic against a bounding box, and what it decides is what a person is
asked -- not what a subprocess is told.

**The orientation is part of the arithmetic, not a detail left to the slicer.**
PrusaSlicer fits each axis against the matching one, so a size worked out from
the better of the two orientations is only true if that orientation is taken.
:func:`scale_offer` therefore returns the one it chose, and the caller is
expected to ask for it.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from cadless.printer_profile import BuildVolume, fmt

#: How much of the bed to leave free around a scaled object, in millimetres.
#:
#: A skirt is drawn beside the part, not on it, so a model scaled to the exact
#: build volume is a model whose skirt does not fit. Split across both sides.
SCALE_MARGIN_MM = 10.0


def _mm(value: float) -> str:
    """A measurement for a sentence somebody reads, rather than for a flag.

    Rounded, because a bounding box carries whatever floating-point noise the
    geometry left in it and "1100.0000000000002 x 600 x 450 mm" reads as a bug in
    the tool. :func:`_fmt` stays exact: it feeds the slicer, where a rounded
    nozzle diameter would be a different print.
    """
    return fmt(round(value, 1))


def _dimensions(bbox: Sequence[Any] | None) -> tuple[float, float, float] | None:
    """The bounding box as three usable numbers, or ``None``.

    ``None`` covers everything that is not three finite numbers: a missing box, a
    short one, a string where a number should be, a NaN. Every caller here
    answers that the same way -- say nothing, and let the slicer be the one to
    speak -- which is why the unpacking is one piece of code rather than three
    copies drifting apart.
    """
    try:
        width, depth, height = (float(value) for value in bbox)  # type: ignore[misc]
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(value) for value in (width, depth, height)):
        return None
    return width, depth, height


@dataclass(frozen=True)
class ScaleOffer:
    """What scaling a too-big model down would give, as something to be asked.

    ``percent`` and ``size`` are worked out from the recorded bounding box and
    are therefore an estimate — the slicer does the real arithmetic against the
    mesh. They agree wherever this offer can appear at all, because the check
    that produces it reads the same box. Said as "about" for that reason.

    ``turned`` is the orientation those numbers assume, and it is here because
    they are only true if it is taken. The slicer scales each axis against the
    matching one, so a model laid the other way round comes out at a different
    size than the one that was agreed to.
    """

    percent: float
    size: tuple[float, float, float]
    turned: bool = False


def scaled_target(volume: BuildVolume) -> BuildVolume:
    """The volume to aim a scaled model at: the bed, less room for a skirt."""
    return BuildVolume(
        width=max(1.0, volume.width - SCALE_MARGIN_MM),
        depth=max(1.0, volume.depth - SCALE_MARGIN_MM),
        height=volume.height,
    )


def scale_offer(bbox: Sequence[Any] | None, volume: BuildVolume) -> ScaleOffer | None:
    """What to offer for a model that will not fit, or ``None`` when there is
    nothing to offer.

    ``None`` for a model that already fits — there is nothing to ask — and for a
    bounding box that is not three usable numbers, which is the same silence
    :func:`too_big_for` keeps.
    """
    if not too_big_for(bbox, volume):
        return None
    dimensions = _dimensions(bbox)
    if dimensions is None:
        return None
    width, depth, height = dimensions
    if not all(value > 0 for value in dimensions):
        return None

    target = scaled_target(volume)
    # Each axis against the matching one, because that is what the slicer does.
    # Both orientations are worked out and the better wins -- and which one won
    # is *returned*, because an offer that assumes a turn nobody takes is a
    # number the reader is shown and then does not get. Measured: PrusaSlicer
    # applies `--rotate` before `--scale-to-fit`, so the turn is the slicer's
    # own starting point and this arithmetic is the arithmetic it will do.
    flat = min(target.width / width, target.depth / depth, target.height / height)
    turned = min(target.width / depth, target.depth / width, target.height / height)
    factor, quarter_turn = (turned, True) if turned > flat else (flat, False)
    if factor >= 1:
        return None

    percent = round(factor * 100, 1)
    size = (round(width * factor, 1), round(depth * factor, 1), round(height * factor, 1))
    # Nothing to offer when the answer rounds away. "About 0%", or a model one of
    # whose sides is 0 mm, is not something a person can agree to, and the button
    # beside it would start a print of nothing.
    if percent <= 0 or any(value <= 0 for value in size):
        return None
    return ScaleOffer(percent=percent, size=size, turned=quarter_turn)


#: A quarter turn about Z, in degrees. The only rotation anything here asks for
#: -- both the allowance :func:`too_big_for` makes and the orientation
#: :func:`scale_offer` assumes are the footprint laid the other way round.
QUARTER_TURN = 90.0


def needs_quarter_turn(bbox: Sequence[Any] | None, volume: BuildVolume) -> bool:
    """Whether this model fits turned a quarter and does not fit as it stands.

    :func:`too_big_for` compares the footprint in both orientations, so it passes
    a model that only fits turned. Nothing was turning it, which made that
    allowance a claim the tool did not keep.
    """
    dimensions = _dimensions(bbox)
    if dimensions is None:
        return False
    width, depth, _height = dimensions
    fits_flat = width <= volume.width and depth <= volume.depth
    fits_turned = depth <= volume.width and width <= volume.depth
    return fits_turned and not fits_flat


def too_big_for(bbox: Sequence[Any] | None, volume: BuildVolume) -> str:
    """Why this model cannot fit, or ``""`` when it might.

    Answered from the bounding box the version already carries, so the reader
    hears it immediately instead of waiting for a slicer to run and then being
    told less. PrusaSlicer's own answer to this is ``All objects are outside of
    the print volume``, which names neither size.

    **Deliberately conservative.** This exists to say something more useful than
    the slicer would, so refusing a model that could be printed is the worse
    error. The footprint is therefore compared in both orientations, because the
    slicer may place a part either way and this check must never be the stricter
    of the two; and a bounding box that is not three usable numbers is not
    refused at all.

    **It cannot see units, and that is a known gap.** ``bbox`` is recorded in the
    project's *authoring* units while the exported STL is always millimetres
    (``cadless/worker.py`` ``export_scale``), so for a domain authored in metres
    -- ``house`` is one -- the numbers here are a thousand times too small and
    nothing is ever refused. That fails **open**: the reader gets the slicer's
    own message instead of this one, which is worse copy and not a wrong answer.
    Closing it needs a version-to-domain link the store does not carry, so it is
    recorded here rather than guessed at.
    """
    dimensions = _dimensions(bbox)
    if dimensions is None:
        return ""
    width, depth, height = dimensions

    footprint = sorted((width, depth))
    bed = sorted((volume.width, volume.depth))
    fits_flat = footprint[0] <= bed[0] and footprint[1] <= bed[1]
    if fits_flat and height <= volume.height:
        return ""
    return (
        f"This model is {_mm(width)} x {_mm(depth)} x {_mm(height)} mm, and the printer's "
        f"build volume is {_mm(volume.width)} x {_mm(volume.depth)} x {_mm(volume.height)} mm. "
        "Ask for a smaller model, or set your printer's real size in Settings."
    )
