"""Drawing a multi-part build: what moves, how far, and in how many frames.

The order search establishes a sequence in which the parts can be brought
together, and the heading it took each one out along. This module turns those
into pictures. It draws rather than decides: every fact it shows was measured in
:mod:`cadless.assembly_check`, and nothing here re-derives one.

An exploded view and a step-by-step sequence are the same drawing. Both move a
set of parts along their headings and render the result; they differ only in
which parts are shown and which of those are moved. So there is one composer
here and a rule that picks how to call it, rather than two renderers that could
drift apart.

Meshes, not solids: the parts have been exported by the time a guide is drawn,
so this needs no OCCT and does not pay for it.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from cadless.catalog.thumbnail import (
    DEFAULT_SIZE,
    isometric_basis,
    projected_extent,
    render_bytes,
)

#: At or above this many parts, the guide is drawn a step at a time rather than
#: as one exploded view.
#:
#: Below it the step frames are near-duplicates of the exploded one. The model is
#: asked for the fewest parts that each fit the printer, so two or three is the
#: ordinary split, and a reader takes the whole of that in at a glance. One place
#: to change if part counts grow.
STEP_FRAME_MIN_PARTS = 4

#: How far a part is pushed out, as a fraction of the whole assembly's largest
#: dimension.
#:
#: Far enough to open a gap at every joint, and no further: the frames of a set
#: share one scale, so a part sent a long way shrinks every other frame to make
#: room for it.
EXPLODE_FRACTION = 0.35

#: Below this a heading is not a direction. A zero-length vector cannot be
#: normalised, and scaling it would move the part nowhere while claiming to move
#: it somewhere.
_HEADING_EPSILON = 1e-9


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

    distance = _explode_distance(part_meshes)
    basis = isometric_basis()
    plans = _frame_plans(count, order)
    drawings = [
        compose(
            [part_meshes[index] for index in shown],
            [_offset(index, releases, moving, distance) for index in shown],
        )
        for _, shown, moving in plans
    ]
    # One extent across the whole set, so a part keeps its size from frame to
    # frame. Fitted per frame, the first would be filled by the one part in it.
    extent = max(projected_extent(drawing, basis) for drawing in drawings)
    return [
        (name, render_bytes(drawing, size, basis=basis, extent=extent))
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
    vector = np.asarray(heading, dtype=np.float64)
    return list(vector / float(np.linalg.norm(vector)) * distance)


def _is_heading(heading: Sequence[float]) -> bool:
    return len(heading) == 3 and float(np.linalg.norm(np.asarray(heading))) > _HEADING_EPSILON


def _explode_distance(meshes: Sequence[np.ndarray]) -> float:
    points = np.concatenate([np.asarray(mesh, dtype=np.float64).reshape(-1, 3) for mesh in meshes])
    return float((points.max(axis=0) - points.min(axis=0)).max()) * EXPLODE_FRACTION
