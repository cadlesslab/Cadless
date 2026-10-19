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

from collections.abc import Sequence

import numpy as np

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
#: of the whole assembly's largest dimension.
#:
#: Far enough to show a gap at every joint, and no further: the frames of a set
#: share one scale, so a part sent a long way shrinks every other frame to make
#: room for it. A part with nothing engaged travels exactly this far.
EXPLODE_FRACTION = 0.35

#: Below this a heading is not a direction. A zero-length vector cannot be
#: normalised, and scaling it would move the part nowhere while claiming to move
#: it somewhere.
_HEADING_EPSILON = 1e-9

#: How close two unit headings have to be to count as one direction.
_SAME_HEADING_TOLERANCE = 1e-6

#: Beyond this share of a unit heading along X, the heading is too near X for X
#: to serve as the second axis its cross product needs; Y is used instead. Any
#: value well short of one keeps the cross product far from zero length.
_NEAR_X = 0.9

#: The file a checked assembly's motion is written to, beside its exports.
MOTION_FILE = "assembly.json"


def motion_payload(
    order: Sequence[int],
    releases: Sequence[Sequence[float]],
    part_meshes: Sequence[np.ndarray] | None = None,
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
    return payload


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

    The length a part is engaged along its heading, plus the spacing every part
    gets. Engaged means: how far it has to go before it is beyond every part
    placed before it that lies across its path -- beyond where that part rests,
    and beyond where that part ends up once it is out too. Both, because the
    part placed before it comes off after it, along a heading of its own; its
    whole way out lies between those two places, so a part beyond both along
    its own heading never meets it. Cleared from only one of them, a pin could
    end inside or against a sleeve that went out the same way, or still be in a
    sleeve that went the other way.

    Across its path means the two overlap seen down the heading. A part off to
    one side is in no way of it, and counting it would send the part out past
    something it never had to clear. The boxes seen down the heading are coarse
    in the safe direction: they can only find more in the way. The reach along
    it is read off mesh vertices, and a curved surface's furthest point can fall
    between them, so it can come out short by the tessellation's deviation; the
    spacing on top, there for the gap a reader sees, is far wider than that.

    Walked in the order the parts go together, so each part placed before this
    one has its travel by the time it is needed. A part with no heading stays
    put and travels nothing, and a part with no triangles has nothing to clear
    and is in no one's way.
    """
    points = [np.asarray(mesh, dtype=np.float64).reshape(-1, 3) for mesh in part_meshes]
    spacing = _largest_dimension(part_meshes) * EXPLODE_FRACTION
    travels = [0.0] * len(points)
    for position, index in enumerate(order):
        if index >= len(releases) or not _is_heading(releases[index]):
            continue
        heading = _unit(releases[index])
        across = _across(heading)
        mine = points[index]
        reach = 0.0
        for other in order[:position]:
            theirs = points[other]
            if not len(mine) or not len(theirs):
                continue
            if travels[other]:
                out = theirs + _unit(releases[other]) * travels[other]
                theirs = np.concatenate([theirs, out])
            if _apart_seen_along(mine @ across.T, theirs @ across.T):
                continue
            reach = max(reach, float((theirs @ heading).max() - (mine @ heading).min()))
        travels[index] = reach + spacing
    return travels


def _across(heading: np.ndarray) -> np.ndarray:
    """Two unit directions square to ``heading`` and to each other, as rows."""
    helper = np.array([0.0, 1.0, 0.0]) if abs(heading[0]) > _NEAR_X else np.array([1.0, 0.0, 0.0])
    first = np.cross(heading, helper)
    first /= float(np.linalg.norm(first))
    return np.stack([first, np.cross(heading, first)])


def _apart_seen_along(mine: np.ndarray, theirs: np.ndarray) -> bool:
    """Whether two point sets, projected on a plane, have boxes that do not meet.

    Boxes that touch count as meeting, so a part exactly as wide as the gap it
    slides through is still treated as in the way.
    """
    return bool(
        np.any(mine.max(axis=0) < theirs.min(axis=0))
        or np.any(theirs.max(axis=0) < mine.min(axis=0))
    )


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
        and float(np.dot(_unit(releases[other]), heading)) >= 1 - _SAME_HEADING_TOLERANCE
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
    return len(heading) == 3 and float(np.linalg.norm(np.asarray(heading))) > _HEADING_EPSILON


def _largest_dimension(meshes: Sequence[np.ndarray]) -> float:
    points = np.concatenate([np.asarray(mesh, dtype=np.float64).reshape(-1, 3) for mesh in meshes])
    if not len(points):
        return 0.0
    return float((points.max(axis=0) - points.min(axis=0)).max())
