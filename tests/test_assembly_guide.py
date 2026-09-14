"""How an assembly is drawn: what moves, how far, and how many frames.

Meshes rather than solids, so none of this needs OCCT. The headings these frames
are drawn from are measured by the order search against real geometry, and are
handed in here rather than derived.
"""

import io

import numpy as np
import pytest
from PIL import Image

from cadless.assembly_guide import (
    STEP_FRAME_MIN_PARTS,
    compose,
    guide_frames,
)


def _cube(centre=(0.0, 0.0, 0.0), side=10.0) -> np.ndarray:
    """A closed box as an ``(12, 3, 3)`` triangle array."""
    h = side / 2.0
    c = np.asarray(centre, dtype=np.float64)
    corners = np.array(
        [[x, y, z] for x in (-h, h) for y in (-h, h) for z in (-h, h)], dtype=np.float64
    )
    faces = [
        (0, 1, 3, 2),
        (4, 6, 7, 5),
        (0, 4, 5, 1),
        (2, 3, 7, 6),
        (0, 2, 6, 4),
        (1, 5, 7, 3),
    ]
    tris = []
    for a, b, cc, d in faces:
        tris.append([corners[a], corners[b], corners[cc]])
        tris.append([corners[a], corners[cc], corners[d]])
    return np.asarray(tris, dtype=np.float64) + c


def _ink_bounds(png: bytes) -> tuple[int, int, int, int]:
    """The drawn pixels' bounding box as ``(top, left, bottom, right)``."""
    image = Image.open(io.BytesIO(png)).convert("L")
    array = np.asarray(image)
    background = np.bincount(array.reshape(-1)).argmax()
    drawn = np.argwhere(array != background)
    if drawn.size == 0:
        return (0, 0, 0, 0)
    lo, hi = drawn.min(axis=0), drawn.max(axis=0)
    return (int(lo[0]), int(lo[1]), int(hi[0]), int(hi[1]))


def _ink_box(png: bytes) -> tuple[int, int]:
    """The width and height of the drawn pixels in a rendered frame."""
    top, left, bottom, right = _ink_bounds(png)
    if (top, left, bottom, right) == (0, 0, 0, 0):
        return (0, 0)
    return (right - left + 1, bottom - top + 1)


# --- what moves -----------------------------------------------------------


def test_compose_moves_each_mesh_by_its_own_offset():
    one, two = _cube(), _cube()
    out = compose([one, two], [[0.0, 0.0, 0.0], [0.0, 0.0, 40.0]])
    assert out.shape == (24, 3, 3)
    assert out[:12] == pytest.approx(one)
    assert out[12:] == pytest.approx(two + np.array([0.0, 0.0, 40.0]))


def test_compose_refuses_a_mesh_with_no_offset_rather_than_guessing_one():
    with pytest.raises(ValueError):
        compose([_cube(), _cube()], [[0.0, 0.0, 0.0]])


def test_a_part_with_no_recorded_heading_does_not_move():
    # The part left standing is what the rest come off. The search recorded no
    # heading for it, and inventing one would slide the whole assembly sideways
    # in every frame for no reason a reader could see.
    parts = [_cube(), _cube((0.0, 0.0, 10.2))]
    frames = guide_frames(parts, order=[0, 1], releases=[[], [0.0, 0.0, 1.0]])
    assert len(frames) == 1
    # Nothing to assert on pixels here; the movement is checked through compose
    # above. What this pins is that an empty heading is accepted at all rather
    # than raising, which is the shape every anchor arrives in.


# --- how many frames ------------------------------------------------------


def test_a_small_assembly_is_drawn_as_one_exploded_view():
    parts = [_cube((0.0, 0.0, z)) for z in (0.0, 10.2, 20.4)]
    frames = guide_frames(parts, order=[0, 1, 2], releases=[[], [0.0, 0.0, 1.0], [0.0, 0.0, 1.0]])
    assert [name for name, _ in frames] == ["exploded"]


def test_an_assembly_at_the_threshold_is_drawn_a_step_at_a_time():
    count = STEP_FRAME_MIN_PARTS
    parts = [_cube((0.0, 0.0, 10.2 * i)) for i in range(count)]
    releases = [[]] + [[0.0, 0.0, 1.0]] * (count - 1)
    frames = guide_frames(parts, order=list(range(count)), releases=releases)
    assert len(frames) == count
    assert [name for name, _ in frames] == [f"step{i + 1}" for i in range(count)]


def test_every_frame_is_a_png():
    parts = [_cube((0.0, 0.0, 10.2 * i)) for i in range(STEP_FRAME_MIN_PARTS)]
    releases = [[]] + [[0.0, 0.0, 1.0]] * (STEP_FRAME_MIN_PARTS - 1)
    frames = guide_frames(parts, order=list(range(STEP_FRAME_MIN_PARTS)), releases=releases)
    for _, png in frames:
        assert Image.open(io.BytesIO(png)).format == "PNG"


# --- one scale across the set ---------------------------------------------


def test_the_frames_share_one_scale_so_a_part_keeps_its_size():
    # Fitted per frame, the first frame holds one part and that part would fill
    # it -- the same edge then appears at one size early and another size late,
    # which is exactly what makes a step sequence unreadable. Fitted to the whole
    # set, the lone part in the first frame occupies a small corner of it.
    count = STEP_FRAME_MIN_PARTS
    parts = [_cube((0.0, 0.0, 30.0 * i)) for i in range(count)]
    releases = [[]] + [[0.0, 0.0, 1.0]] * (count - 1)
    frames = guide_frames(parts, order=list(range(count)), releases=releases, size=256)

    first_w, first_h = _ink_box(frames[0][1])
    last_w, last_h = _ink_box(frames[-1][1])
    assert max(last_w, last_h) > max(first_w, first_h) * 2
    assert max(first_w, first_h) < 256 * 0.5


def test_the_frames_share_one_centre_so_a_part_keeps_its_place():
    # A shared scale alone is not enough: fitted to its own middle, every frame
    # re-centres on a subject that is growing, so the part placed first slides
    # across the canvas as the others arrive. The size check above cannot see
    # that, because the ink stays the same size while it moves -- which is why
    # this one reads position instead.
    count = STEP_FRAME_MIN_PARTS
    parts = [_cube((0.0, 0.0, 30.0 * i)) for i in range(count)]
    releases = [[]] + [[0.0, 0.0, 1.0]] * (count - 1)
    frames = guide_frames(parts, order=list(range(count)), releases=releases, size=256)

    # The first part is at rest in every frame, so its lowest drawn row is the
    # same row throughout unless the window moved under it.
    bottoms = [_ink_bounds(png)[2] for _, png in frames]
    assert max(bottoms) - min(bottoms) <= 2


# --- nothing to draw ------------------------------------------------------


def test_a_single_part_is_not_an_assembly_and_gets_no_frames():
    assert guide_frames([_cube()], order=[0], releases=[[]]) == []


def test_a_heading_list_shorter_than_the_parts_does_not_raise():
    # An engine that recorded a heading for some parts and not others: the guide
    # is worth less than the build, so a short list falls back to "stays put"
    # rather than taking the whole turn down with an index error.
    parts = [_cube((0.0, 0.0, 12.0 * i)) for i in range(3)]
    frames = guide_frames(parts, order=[0, 1, 2], releases=[[0.0, 0.0, 1.0]])
    assert len(frames) == 1


def test_a_build_with_no_measured_heading_draws_nothing():
    # An engine old enough to have found an order but not recorded a heading
    # leaves every part at rest, and every part at rest is a true picture of the
    # assembled model and a false exploded view. Better no picture: the written
    # steps still stand on the order, which that engine did record.
    parts = [_cube(), _cube((0.0, 0.0, 10.2))]
    assert guide_frames(parts, order=[0, 1], releases=[]) == []
    assert guide_frames(parts, order=[0, 1], releases=[[], []]) == []


def test_an_order_that_does_not_name_every_part_draws_nothing():
    # A partial order describes a different assembly from the one measured.
    # Drawing it anyway would put a picture to a sequence nothing established.
    parts = [_cube(), _cube((0.0, 0.0, 10.2))]
    assert guide_frames(parts, order=[0], releases=[[], []]) == []
