"""Drawing a multi-part build: what moves, how far, and in how many frames.

The order search establishes a sequence in which the parts can be brought
together, and the heading it took each one out along. This module turns those
into pictures. It draws rather than decides: every fact it shows was measured
upstream and handed in, and nothing here re-derives one.

An exploded view and a step-by-step sequence are the same drawing. Both move a
set of parts along their headings and render the result; they differ only in
which parts are shown and which of those are moved. So there is one composer
here and a rule that picks how to call it, rather than two renderers that could
drift apart.

Meshes, not solids: this draws from exported mesh files, so it needs no geometry
kernel and does not pay for one.
"""

from __future__ import annotations

import math
import numbers
from collections.abc import Mapping, Sequence
from collections.abc import Set as AbstractSet

import numpy as np

from cadless.assembly_motion import HEADING_EPSILON, SAME_HEADING_TOLERANCE, require_motion
from cadless.assembly_motion import MOTION_FILE as MOTION_FILE  # where callers have always found it
from cadless.catalog.thumbnail import (
    DEFAULT_SIZE,
    isometric_basis,
    projected_centre,
    projected_extent,
    render_bytes,
)

#: At or above this many parts, the guide is drawn a step at a time rather than
#: as one exploded view.
#:
#: Below it a per-step frame adds a picture without adding information: a split
#: small enough to be taken in at a glance makes every step frame a near-duplicate
#: of the exploded one. One place to change if part counts grow.
STEP_FRAME_MIN_PARTS = 4

#: The gap a part is pushed out by once it has cleared its joint, as a fraction
#: of the largest dimension of the parts measured together: the whole assembly,
#: or a sub-assembly's members when what is measured is how far they come off
#: one another.
#:
#: Far enough to show a gap at every joint, and no further: the frames of a set
#: share one scale, so a part sent a long way shrinks every other frame to make
#: room for it. A part with nothing engaged travels exactly this far.
EXPLODE_FRACTION = 0.35

#: How far the parts may spread once every one of them is out, as a multiple of
#: the largest dimension of the parts measured together, before the spacing is
#: cut to fit. Measured with each part moved by its own travel, as the guide
#: draws them: a viewer playing a grouped motion takes a sub-assembly off whole,
#: as far as its furthest member goes, and then apart, which can spread past the
#: bound by up to the sub-assembly's own spread as it comes apart. A
#: sub-assembly's members coming off one another are held to their own size, not
#: to the room the whole model leaves.
#:
#: A part that has to clear one already out clears that part's spacing too, so
#: down a chain of parts leaving the same way the spacing is counted once per
#: link: a long chain spreads the model many times its size and leaves each
#: piece a speck in the frame that has to hold them all. Only the spacing is
#: cut — what each part has to clear never is, so every joint still opens.
MAX_SPREAD = 2.5

#: The least spacing a part keeps however long its chain, as a fraction of the
#: largest dimension of the parts measured together: below it a joint reads as
#: closed, and a chain that cannot fit the bound with it spreads further instead.
MIN_EXPLODE_FRACTION = 0.02

#: How many halvings the spacing is fitted by — far finer than any difference a
#: reader could see between two spreads.
_FIT_STEPS = 8

#: Beyond this share of a unit heading along X, the heading is too near X for X
#: to serve as the second axis its cross product needs; Y is used instead. Any
#: value well short of one keeps the cross product far from zero length.
_NEAR_X = 0.9


def motion_payload(
    order: Sequence[int],
    releases: Sequence[Sequence[float]],
    part_meshes: Sequence[np.ndarray] | None = None,
    *,
    steps: Sequence[Sequence[int]] | None = None,
    subassemblies: Sequence[Mapping[str, Sequence]] | None = None,
) -> dict:
    """The measured order and headings, in the form the viewport plays them from.

    The same facts the frames are drawn from, handed on rather than drawn.

    ``releases`` comes back with one entry per part, filed under the part's own
    index rather than its place in the order. A part with no heading -- the one
    left standing, or every part when the engine that measured the build did not
    record headings -- gets an empty entry, which means "stays put", rather than
    a direction nobody measured or a list that runs short of the parts.

    ``travel`` is, per part index, how far the part goes out, as a fraction of
    the assembled model's largest dimension: the distances the frames are drawn
    with, so a player that moves each part by them moves it as far as a frame
    does. A fraction rather than millimetres because the viewport draws in other
    units and measures the model it draws. It is there only when ``part_meshes``
    are the parts the order names, because a travel measured from other meshes
    would move these parts by another model's joints, and only when they have a
    finite size to be a fraction of.

    ``explode_fraction`` and ``stretch`` are the spacing a file without ``travel``
    is played by, by a viewer that predates it and by any viewer when nothing was
    measured: per part index, ``stretch`` counts how many of those fractions the
    part goes out once every part is apart, so pieces leaving the same way do not
    stop at the same place.

    ``steps`` and ``subassemblies`` group what the parts do, for a build whose
    author knows more than one order can say: ``steps`` cuts the order into runs
    whose parts move at once, and each sub-assembly -- ``{"parts": [...],
    "splits": [...]}``, a heading or ``[]`` per member -- comes off whole and then
    apart. Each is written with ``split_travel`` added: how far each member comes
    off the rest, measured as a part's travel is measured, from the members' own
    meshes in the order they go together and along their splits, and filed as a
    fraction of the whole model's largest dimension, as ``travel`` is. A member
    that stays with the rest gets none. Measured means ``part_meshes`` are needed,
    and have to be the parts the order names with a finite size to them; without
    them this raises rather than guess.

    A grouped motion is checked before it is returned, against one part for each
    entry of the order, and raises ``ValueError`` naming every problem rather
    than return a file the viewport would not play: the grouping is an author's
    word rather than something measured here, and a refused file plays as no
    player at all. A motion without either is returned as it always was, whatever
    it holds.
    """
    headings = [
        [float(c) for c in releases[index]]
        if index < len(releases) and _is_heading(releases[index])
        else []
        for index in range(len(order))
    ]
    payload = {
        "order": [int(index) for index in order],
        "releases": headings,
        "stretch": [_stretch(index, order, headings) for index in range(len(order))],
        "explode_fraction": EXPLODE_FRACTION,
    }
    if part_meshes is not None and sorted(order) == list(range(len(part_meshes))):
        # A vertex out at infinity, or no vertex at all, leaves nothing a travel
        # could be a fraction of; the file keeps the spacing alone.
        size = _largest_dimension(part_meshes)
        if np.isfinite(size) and size > 0:
            travels = _travels(part_meshes, order, headings)
            payload["travel"] = [travel / size for travel in travels]
    if steps is None and subassemblies is None:
        return payload
    if steps is not None:
        payload["steps"] = [_ordinals(run) for run in steps]
    if subassemblies is not None:
        payload["subassemblies"] = _subassemblies(order, subassemblies, part_meshes)
    # One part for each entry of the order, as the viewport counts the parts it
    # draws: counted from the parts the order names instead, an order naming one
    # part twice would be checked against itself and pass.
    return require_motion(payload, range(len(order)))


def _subassemblies(
    order: Sequence[int],
    subassemblies: Sequence[Mapping[str, Sequence]],
    part_meshes: Sequence[np.ndarray] | None,
) -> list[dict]:
    """Each sub-assembly as the file holds it, with how far each member comes off.

    Members and splits are written as numbers, the way the order and the
    headings are, but left in the shape they came in -- a split of the wrong
    length, a member the order does not name, a member that is not a whole
    number -- for the check that follows to name, rather than tidied here into
    something nobody was told about. Only one with no list of parts or of splits
    to write is refused here, there being nothing to leave for the check.
    """
    written = []
    for position, sub in enumerate(subassemblies):
        parts = _listed(sub.get("parts")) if isinstance(sub, Mapping) else None
        splits = _listed(sub.get("splits")) if isinstance(sub, Mapping) else None
        if parts is None or splits is None:
            raise ValueError(
                f"this grouping is not a motion the viewport plays: sub-assembly {position} "
                "is not a mapping with a list of parts and a list of splits"
            )
        written.append({"parts": _ordinals(parts), "splits": [_numbers(s) for s in splits]})
    if not written:
        return written
    if part_meshes is None or sorted(order) != list(range(len(part_meshes))):
        raise ValueError(
            "a sub-assembly needs the meshes of the parts the order names: "
            "how far each member comes off it is measured from them"
        )
    size = _largest_dimension(part_meshes)
    if not (np.isfinite(size) and size > 0):
        raise ValueError(
            "the parts' meshes have no finite size for a sub-assembly's travel to be a fraction of"
        )
    for sub in written:
        sub["split_travel"] = [travel / size for travel in _split_travels(part_meshes, order, sub)]
    return written


def _split_travels(
    part_meshes: Sequence[np.ndarray], order: Sequence[int], sub: Mapping[str, list]
) -> list[float]:
    """How far each member of ``sub`` comes off the rest, in the meshes' units.

    The members alone, taken in the order they go together and moved along
    their splits: what a member has to clear as it leaves is the members placed
    before it, the sub-assembly being off the model by then. A member the order
    does not name is not measured and goes nowhere.
    """
    members = sub["parts"]
    if not all(isinstance(member, int) and not isinstance(member, bool) for member in members):
        # Nothing to measure a member by that is not a part's number; the check
        # that follows names it.
        return [0.0] * len(members)
    split_of = {
        member: _measurable(sub["splits"][position]) if position < len(sub["splits"]) else []
        for position, member in enumerate(members)
    }
    placed = [index for index in order if index in split_of]
    travels = _travels(
        [part_meshes[index] for index in placed],
        list(range(len(placed))),
        [split_of[index] for index in placed],
    )
    measured = dict(zip(placed, travels, strict=True))
    return [measured.get(member, 0.0) for member in members]


def _listed(value: object) -> list | None:
    """``value``'s entries as a list, if it holds them in an order -- a list, a
    tuple, an array, a generator, whatever a caller built its grouping from --
    and ``None`` otherwise: for a string, bytes or a mapping, which hold no
    entries of this kind, and for a set, whose order is no order, since members
    are paired with their splits by position."""
    if isinstance(value, (str, bytes, bytearray, memoryview, Mapping, AbstractSet)):
        return None
    try:
        entries = iter(value)
    except TypeError:
        return None
    return list(entries)


def _ordinals(run: object) -> object:
    """A run or a sub-assembly's members with each whole number written as an
    ``int``, and anything else -- a fraction, ``True``, a run that is not a
    sequence -- left as it came, for the check to name rather than this to round
    into a part nobody named."""
    listed = _listed(run)
    if listed is None:
        return run
    return [_ordinal(value) for value in listed]


def _ordinal(value: object) -> object:
    if isinstance(value, bool):
        return value
    if isinstance(value, numbers.Integral):
        return int(value)
    if isinstance(value, numbers.Real) and float(value).is_integer():
        return int(value)
    return value


def _numbers(split: object) -> object:
    """A split with each number in it written as a ``float``, otherwise as it came."""
    listed = _listed(split)
    if listed is None:
        return split
    return [
        float(c) if isinstance(c, numbers.Real) and not isinstance(c, bool) else c for c in listed
    ]


def _measurable(split: object) -> list:
    """A split a member can be moved along -- three finite numbers -- or ``[]``,
    which moves it nowhere: the check that follows names a split that is neither."""
    if (
        isinstance(split, list)
        and len(split) == 3
        and all(isinstance(c, float) and math.isfinite(c) for c in split)
    ):
        return split
    return []


def compose(meshes: Sequence[np.ndarray], offsets: Sequence[Sequence[float]]) -> np.ndarray:
    """The meshes as one triangle array, each moved by its own offset."""
    if len(meshes) != len(offsets):
        raise ValueError(f"{len(meshes)} meshes to draw but {len(offsets)} offsets for them")
    if not meshes:
        return np.empty((0, 3, 3), dtype=np.float64)
    return np.concatenate(
        [
            np.asarray(meshes[index], dtype=np.float64)
            + np.asarray(offsets[index], dtype=np.float64)
            for index in range(len(meshes))
        ]
    )


def guide_frames(
    part_meshes: Sequence[np.ndarray],
    order: Sequence[int],
    releases: Sequence[Sequence[float]],
    size: int = DEFAULT_SIZE,
) -> list[tuple[str, bytes]]:
    """The frames a guide shows, as ``(name, PNG bytes)``.

    Nothing is drawn where there is nothing established to draw: a build that is
    not an assembly, an order that does not name every part, or a set of headings
    with no direction in it. A picture of a sequence the engine never measured
    would be the one part of a guide a reader cannot check.
    """
    count = len(part_meshes)
    if count < 2 or sorted(order) != list(range(count)):
        return []
    if not any(_is_heading(heading) for heading in releases):
        # Every part at rest is the assembled model, which is a true picture and
        # a false exploded view. The written steps still stand on the order.
        return []

    travels = _travels(part_meshes, order, releases)
    basis = isometric_basis()
    plans = _frame_plans(count, order)
    drawings = [
        compose(
            [part_meshes[index] for index in shown],
            _frame_offsets(shown, releases, moving, travels),
        )
        for _, shown, moving in plans
    ]
    # One window across the whole set: the same scale, so a part keeps its size
    # from frame to frame, and the same centre, so it keeps its place. A shared
    # scale alone is not enough -- each frame would then re-centre on a subject
    # that is growing, sliding a part that has not moved. Fitted per frame it
    # would also be blown up to fill the first frame it appears alone in.
    union = np.concatenate(drawings)
    extent = projected_extent(union, basis)
    centre = projected_centre(union, basis)
    return [
        (name, render_bytes(drawing, size, basis=basis, extent=extent, centre=centre))
        for (name, _, _), drawing in zip(plans, drawings, strict=True)
    ]


def _frame_plans(count: int, order: Sequence[int]) -> list[tuple[str, list[int], set[int]]]:
    """Each frame's name, the parts it shows, and which of those are moved."""
    if count < STEP_FRAME_MIN_PARTS:
        return [("exploded", list(order), set(order))]
    # A step shows what is already together plus the one part going on next, so
    # a reader sees the joint being made rather than the finished object.
    return [
        (f"step{position + 1}", list(order[: position + 1]), {order[position]})
        for position in range(count)
    ]


def _frame_offsets(
    shown: Sequence[int],
    releases: Sequence[Sequence[float]],
    moving: set[int],
    travels: Sequence[float],
) -> list[list[float]]:
    """Where each shown part sits in one frame, in the order they are shown.

    A moved part goes its own travel out along its heading, and every other part
    stays where it belongs. One travel per part whichever frame it is drawn in:
    measured to clear the parts placed before it both at rest and fully out, it
    clears them in a step frame, where they are at rest, and in the exploded one,
    where they are out.
    """
    return [_offset(index, releases, moving, travels[index]) for index in shown]


def _travels(
    part_meshes: Sequence[np.ndarray],
    order: Sequence[int],
    releases: Sequence[Sequence[float]],
) -> list[float]:
    """How far each part goes out, in the meshes' own units, per part index.

    What each part has to clear, plus a spacing every part gets: the usual one,
    or — where the parts out at it, each moved by its own travel as the guide
    draws them, would spread further than ``MAX_SPREAD`` of the assembly's size
    — a smaller one found by halving down to ``MIN_EXPLODE_FRACTION``: the
    largest of those tried whose spread stays within the bound. Halving assumes
    the spread grows with the spacing, as it does down a chain; a part leaving
    inward from an edge can shrink it, so the spacing found need not be the
    largest that would fit. Where none tried fits, the floor is kept, and the
    parts spread as far as it takes them — past the bound when even the floor
    does not fit.

    Each part's vertices are read once, into boxes: everything a travel is
    measured from is the extremes of a part along its heading and across it,
    and a part out is the same box moved, so trying another spacing costs no
    second pass over the meshes.
    """
    points = [np.asarray(mesh, dtype=np.float64).reshape(-1, 3) for mesh in part_meshes]
    size = _largest_dimension(part_meshes)
    seen = _seen_along(points, releases)
    extents = [(mine.min(axis=0), mine.max(axis=0)) if len(mine) else None for mine in points]
    travels = _spaced_travels(seen, len(points), order, releases, size * EXPLODE_FRACTION)
    if (
        not (np.isfinite(size) and size > 0)
        or _spread(extents, releases, travels) <= MAX_SPREAD * size
    ):
        return travels
    fits, cut = MIN_EXPLODE_FRACTION, EXPLODE_FRACTION
    fitted = None
    for _ in range(_FIT_STEPS):
        middle = (fits + cut) / 2
        candidate = _spaced_travels(seen, len(points), order, releases, size * middle)
        if _spread(extents, releases, candidate) <= MAX_SPREAD * size:
            fits, fitted = middle, candidate
        else:
            cut = middle
    if fitted is None:
        return _spaced_travels(seen, len(points), order, releases, size * MIN_EXPLODE_FRACTION)
    return fitted


#: Per unit heading: the heading and the two directions across it as rows, and
#: each part's lowest and highest point along those three, or ``None`` for a
#: part with no triangles.
_Seen = dict[tuple[float, ...], tuple[np.ndarray, list[tuple[np.ndarray, np.ndarray] | None]]]


def _seen_along(points: Sequence[np.ndarray], releases: Sequence[Sequence[float]]) -> _Seen:
    """Every part seen down every heading a part leaves along, read once."""
    seen: _Seen = {}
    for index, release in enumerate(releases):
        if index >= len(points) or not _is_heading(release):
            continue
        heading = _unit(release)
        key = tuple(heading)
        if key in seen:
            continue
        axes = np.vstack([heading, _across(heading)])
        boxes: list[tuple[np.ndarray, np.ndarray] | None] = []
        for mine in points:
            if len(mine):
                along = mine @ axes.T
                boxes.append((along.min(axis=0), along.max(axis=0)))
            else:
                boxes.append(None)
        seen[key] = (axes, boxes)
    return seen


def _spaced_travels(
    seen: _Seen,
    count: int,
    order: Sequence[int],
    releases: Sequence[Sequence[float]],
    spacing: float,
) -> list[float]:
    """How far each part goes out with ``spacing`` past what it has to clear.

    The length a part is engaged along its heading, plus the spacing. Engaged
    means: how far it has to go before it is beyond every part placed before it
    that lies across its path -- beyond where that part rests, and beyond where
    that part ends up once it is out too. Both, because the part placed before
    it comes off after it, along a heading of its own; its whole way out lies
    between those two places, so a part beyond both along its own heading never
    meets it. Cleared from only one of them, a pin could end inside or against a
    sleeve that went out the same way, or still be in a sleeve that went the
    other way.

    Across its path means the two overlap seen down the heading. A part off to
    one side is in no way of it, and counting it would send the part out past
    something it never had to clear. The boxes seen down the heading are coarse
    in the safe direction: they can only find more in the way. The reach along
    it is read off mesh vertices, and a curved surface's furthest point can fall
    between them, so it can come out short by the tessellation's deviation; the
    spacing on top, there for the gap a reader sees, is wider than that.

    Walked in the order the parts go together, so each part placed before this
    one has its travel by the time it is needed. A part with no heading stays
    put and travels nothing, a part with no triangles has nothing to clear and
    is in no one's way, and a part whose heading has no finite direction, seen
    along nothing, keeps the spacing alone.
    """
    travels = [0.0] * count
    for position, index in enumerate(order):
        if index >= len(releases) or index >= count or not _is_heading(releases[index]):
            continue
        entry = seen.get(tuple(_unit(releases[index])))
        if entry is None:
            travels[index] = spacing
            continue
        axes, boxes = entry
        mine = boxes[index]
        reach = 0.0
        for other in order[:position]:
            theirs = boxes[other] if other < count else None
            if mine is None or theirs is None:
                continue
            low, high = theirs
            if travels[other]:
                # Out, it is the same box moved along its own heading.
                shift = (axes @ _unit(releases[other])) * travels[other]
                low, high = np.minimum(low, low + shift), np.maximum(high, high + shift)
            # Apart seen down the heading when the boxes across it do not meet;
            # boxes that touch count as meeting, so a part exactly as wide as
            # the gap it slides through is still treated as in the way.
            if np.any(mine[1][1:] < low[1:]) or np.any(high[1:] < mine[0][1:]):
                continue
            reach = max(reach, float(high[0] - mine[0][0]))
        travels[index] = reach + spacing
    return travels


def _spread(
    extents: Sequence[tuple[np.ndarray, np.ndarray] | None],
    releases: Sequence[Sequence[float]],
    travels: Sequence[float],
) -> float:
    """The largest dimension of every part moved out by its own travel.

    A part whose heading has no finite direction is measured where it rests:
    out along nothing, it would make the spread no number at all, which no
    spacing fits.
    """
    lows, highs = [], []
    for index, extent in enumerate(extents):
        if extent is None:
            continue
        low, high = extent
        if travels[index]:
            shift = _unit(releases[index]) * travels[index]
            if np.all(np.isfinite(shift)):
                low, high = low + shift, high + shift
        lows.append(low)
        highs.append(high)
    if not lows:
        return 0.0
    return float((np.max(highs, axis=0) - np.min(lows, axis=0)).max())


def _across(heading: np.ndarray) -> np.ndarray:
    """Two unit directions square to ``heading`` and to each other, as rows."""
    helper = np.array([0.0, 1.0, 0.0]) if abs(heading[0]) > _NEAR_X else np.array([1.0, 0.0, 0.0])
    first = np.cross(heading, helper)
    first /= float(np.linalg.norm(first))
    return np.stack([first, np.cross(heading, first)])


def _stretch(index: int, order: Sequence[int], releases: Sequence[Sequence[float]]) -> int:
    """How many spacings a part goes out with every part apart, for a file
    played without a measured travel; see :func:`motion_payload`."""
    if index not in order or index >= len(releases) or not _is_heading(releases[index]):
        return 1
    heading = _unit(releases[index])
    placed_before = list(order)[: list(order).index(index)]
    return 1 + sum(
        1
        for other in placed_before
        if other < len(releases)
        and _is_heading(releases[other])
        and float(np.dot(_unit(releases[other]), heading)) >= 1 - SAME_HEADING_TOLERANCE
    )


def _offset(
    index: int, releases: Sequence[Sequence[float]], moving: set[int], distance: float
) -> list[float]:
    """Where one part sits in a frame: out along its heading, or where it belongs."""
    if index not in moving or index >= len(releases):
        return [0.0, 0.0, 0.0]
    heading = releases[index]
    if not _is_heading(heading):
        # The part left standing has no heading, and neither has any part when an
        # older engine measured the build. Both belong where they are.
        return [0.0, 0.0, 0.0]
    return list(_unit(heading) * distance)


def _unit(heading: Sequence[float]) -> np.ndarray:
    vector = np.asarray(heading, dtype=np.float64)
    return vector / float(np.linalg.norm(vector))


def _is_heading(heading: Sequence[float]) -> bool:
    return len(heading) == 3 and float(np.linalg.norm(np.asarray(heading))) > HEADING_EPSILON


def _largest_dimension(meshes: Sequence[np.ndarray]) -> float:
    points = np.concatenate([np.asarray(mesh, dtype=np.float64).reshape(-1, 3) for mesh in meshes])
    if not len(points):
        return 0.0
    return float((points.max(axis=0) - points.min(axis=0)).max())
