"""The sizes of the joints :func:`cadless.joinery.split_and_join` builds, without the kernel.

The joinery imports build123d at the top, and the api process keeps the kernel
out, yet the brief has to tell the model how far a joint reaches and how much
solid material it needs, and the checks have to count with the same numbers.
So they live here, and the joinery builds from them: stated in one place, the
size the model is told is the size it gets.
"""

from __future__ import annotations

import math

#: Off square, each flank of a dovetail: enough to lock the halves across the
#: seam. Swept along an upright axis, a dovetail prints without support; along a
#: level one, how its flanks print depends on how the part lies on the bed.
FLANK_DEGREES = 15.0
#: A dovetail's root, across the seam, at most and at least (mm). Below the least
#: it is too weak to hold the halves together.
ROOT_MOST, ROOT_LEAST = 20.0, 3.0
#: How deep a dovetail reaches into the other half, per unit of its root.
DEPTH_PER_ROOT = 0.6
#: A peg's diameter, at most and at least (mm).
PEG_MOST, PEG_LEAST = 8.0, 3.0
#: The shortest a joint may run along the axis the half goes in (mm).
RUN_LEAST = 3.0
#: How close, in mm, the joinery finds a band's edge: far inside the tenth of a
#: millimetre the least band is stated to.
EDGE_PRECISION_MM = 1e-4

#: How far past the cut a joint stands, at most, out of the half behind the
#: plane and into the half ahead (mm): a dovetail at its widest root reaches its
#: depth, a peg at its widest its diameter. The half behind therefore measures up
#: to this much more across the plane than the cut alone leaves it, which is what
#: a split sized to the bed by the cut alone missed.
REACH_MOST_MM = max(ROOT_MOST * DEPTH_PER_ROOT, PEG_MOST)


def pieces_along(extent: float, limit: float) -> int:
    """How many pieces an ``extent`` needs along one axis of a bed ``limit`` long.

    Every seam adds its joint's reach to the piece behind it, so ``n`` pieces
    span at most ``n * limit - (n - 1) * REACH_MOST_MM``: counted by the ratio
    alone, a part just under twice the bed was told two pieces, and the piece
    carrying the joint came back over the bed. Where the limit leaves no more
    than the reach past a joint -- twice the reach or less -- the count runs away
    as the two close, to hundreds of thousands of pieces a hair over the reach,
    so the ratio is given there instead: a smaller count than the joints need,
    on beds far smaller than any printer's, rather than one nobody could follow.
    """
    if extent <= limit:
        return 1
    if limit <= 2 * REACH_MOST_MM:
        return math.ceil(extent / limit)
    return math.ceil((extent - REACH_MOST_MM) / (limit - REACH_MOST_MM))


def margin_mm(clearance: float) -> float:
    """What a socket keeps clear of every edge and hole, beyond its clearance (mm)."""
    return max(1.0, 2 * clearance)


def dovetail_reach(root: float, grow: float) -> float:
    """How far to either side of its centre a dovetail ``root`` wide, grown by
    ``grow`` on its flanks and tip, reaches across the seam at its widest."""
    slope = math.tan(math.radians(FLANK_DEGREES))
    depth = root * DEPTH_PER_ROOT + grow
    return root / 2 + grow / math.cos(math.radians(FLANK_DEGREES)) + depth * slope


def least_band_mm(clearance: float) -> float:
    """How wide a band of the cut face the narrowest dovetail needs at ``clearance``:
    its least root, with its socket grown by the clearance and the margin."""
    return 2 * dovetail_reach(ROOT_LEAST, clearance + margin_mm(clearance))


def least_peg_spot_mm(clearance: float) -> float:
    """How wide a solid spot of the cut face the narrowest peg needs at ``clearance``:
    its least diameter, with the clearance and the margin all round."""
    return PEG_LEAST + 2 * (clearance + margin_mm(clearance))


def stated_mm(least: float) -> float:
    """A least size as it is told to the model: rounded up to a tenth of a millimetre.

    Up rather than to the nearest, because the figure is a floor the model builds
    to: a wall made exactly as thick as a figure rounded down is the one size the
    joint does not fit.
    """
    return math.ceil(least * 10) / 10


def stated_band_mm(clearance: float) -> float:
    """The band a dovetail needs at ``clearance``, as the brief and the refusal say it.

    The joinery finds a band's edges to within :data:`EDGE_PRECISION_MM` on each
    side, so it can read a wall that much short twice over; the figure covers
    that before it is rounded up, or at a clearance whose band rounds up by less,
    a wall made exactly to it would be refused.
    """
    return stated_mm(least_band_mm(clearance) + 2 * EDGE_PRECISION_MM)


def stated_spot_mm(clearance: float) -> float:
    """The spot a peg needs at ``clearance``, as the brief and the refusal say it.

    The joinery finds the roomiest spot to within :data:`EDGE_PRECISION_MM`, so
    the figure covers that across the spot's width before it is rounded up, as
    :func:`stated_band_mm` covers a band's edges.
    """
    return stated_mm(least_peg_spot_mm(clearance) + 2 * EDGE_PRECISION_MM)
