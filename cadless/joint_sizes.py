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

#: How far past the cut a joint stands, at most, out of the half behind the
#: plane and into the half ahead (mm): a dovetail at its widest root reaches its
#: depth, a peg at its widest its diameter. The half behind therefore measures up
#: to this much more across the plane than the cut alone leaves it, which is what
#: a split sized to the bed by the cut alone missed.
REACH_MOST_MM = max(ROOT_MOST * DEPTH_PER_ROOT, PEG_MOST)


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
