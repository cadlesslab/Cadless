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
    _frame_offsets,
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


# A beam split end to end: part 2 stays, and parts 1 and then 0 were slid on
# along +X, so both come off along -X, part 0 first.
_CHAIN_ORDER = [2, 1, 0]
_CHAIN_RELEASES = [[-1.0, 0.0, 0.0], [-1.0, 0.0, 0.0], []]


def test_parts_leaving_in_different_directions_each_go_the_usual_spacing():
    # A lid lifted off and a side panel slid out already part company; pushing
    # the later one further would only shrink every frame to fit it.
    from cadless.assembly_guide import motion_payload

    meshes = [
        _block((0, 0, 0), (10, 10, 10)),
        _block((0, 0, 10), (10, 10, 12)),
        _block((10, 0, 0), (12, 10, 10)),
    ]
    payload = motion_payload([0, 1, 2], [[], [0.0, 0.0, 1.0], [1.0, 0.0, 0.0]], meshes)
    assert payload["travel"] == pytest.approx([0.0, 0.35, 0.35])


def test_the_motion_still_says_how_many_spacings_each_part_goes():
    # A viewer that predates the measured travel spaces the parts by these
    # counts, so a new file still plays there, joints rejoined as before.
    from cadless.assembly_guide import motion_payload

    assert motion_payload(_CHAIN_ORDER, _CHAIN_RELEASES)["stretch"] == [2, 1, 1]


def test_a_part_moved_alone_in_its_frame_goes_its_own_travel():
    # A step frame moves only the part going on; the pieces placed before it stay
    # where they are, and so does a piece with travel that is not moving.
    offsets = _frame_offsets(_CHAIN_ORDER, _CHAIN_RELEASES, {0}, [21.0, 10.5, 0.0])
    assert offsets == [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [-21.0, 0.0, 0.0]]


# --- how far each part goes, measured -------------------------------------


def _block(lo, hi) -> np.ndarray:
    """An axis-aligned box from two corners, as a triangle array."""
    lo, hi = np.asarray(lo, dtype=np.float64), np.asarray(hi, dtype=np.float64)
    return _cube(side=1.0) * (hi - lo) + (lo + hi) / 2.0


def _travel_mm(payload: dict, part: int, meshes) -> float:
    points = np.concatenate([m.reshape(-1, 3) for m in meshes])
    return payload["travel"][part] * float((points.max(axis=0) - points.min(axis=0)).max())


def test_a_pin_is_moved_clear_of_the_bore_it_runs_through():
    # A pin longer than the spacing sits deeper in its joint than a fixed share
    # of the model would take it, and the next part to leave then passes through
    # it on screen. Moved by its own measure, it comes all the way out.
    from cadless.assembly_guide import motion_payload

    block = _block((-20, -20, 0), (20, 20, 10))
    pin = _block((-2, -2, -30), (2, 2, 60))
    meshes = [block, pin]
    payload = motion_payload([0, 1], [[], [0.0, 0.0, 1.0]], meshes)
    assert -30 + _travel_mm(payload, 1, meshes) > 10


def test_the_deepest_part_in_the_way_decides_how_far_a_part_goes():
    # The pin runs through a tall block placed first and a thin plate slid on
    # from below after it. The block is what holds the pin longest, so it is the
    # block the pin has to clear, whichever part was placed last.
    from cadless.assembly_guide import motion_payload

    block = _block((-20, -20, 0), (20, 20, 50))
    plate = _block((-20, -20, -5), (20, 20, 0))
    pin = _block((-2, -2, -10), (2, 2, 60))
    meshes = [block, plate, pin]
    payload = motion_payload([0, 1, 2], [[], [0.0, 0.0, -1.0], [0.0, 0.0, 1.0]], meshes)
    assert -10 + _travel_mm(payload, 2, meshes) > 50


def test_a_measured_chain_keeps_every_joint_open():
    # The beam split end to end again, now measured: the piece that leaves first
    # has to stop beyond where the next one stops, or the two sit joined.
    from cadless.assembly_guide import motion_payload

    meshes = [
        _block((-20, 0, 0), (-10, 5, 5)),
        _block((-10, 0, 0), (0, 5, 5)),
        _block((0, 0, 0), (10, 5, 5)),
    ]
    payload = motion_payload(_CHAIN_ORDER, _CHAIN_RELEASES, meshes)
    assert payload["travel"] == pytest.approx([0.7, 0.35, 0.0])


def test_a_part_off_the_path_does_not_push_a_part_further():
    # A tall post beside the lid is in no way of it. Counting it would send the
    # lid out past the top of the post for no joint a reader can see.
    from cadless.assembly_guide import motion_payload

    meshes = [
        _block((0, 0, 0), (10, 10, 10)),
        _block((30, 0, 0), (35, 10, 50)),
        _block((0, 0, 10), (10, 10, 12)),
    ]
    payload = motion_payload([0, 1, 2], [[], [], [0.0, 0.0, 1.0]], meshes)
    assert payload["travel"] == pytest.approx([0.0, 0.0, 0.35])


def test_a_part_clears_where_an_earlier_part_ended_up_not_only_where_it_was():
    # The pin goes on last and comes off first, but the sleeve it runs through
    # was slid in along the same axis before it, seated a little into the base.
    # Cleared only from the sleeve at rest, the pin would stop inside the sleeve
    # once the sleeve is out too.
    from cadless.assembly_guide import motion_payload

    base = _block((-20, -20, -5), (20, 20, 0))
    sleeve = _block((-4, -4, -3), (4, 4, 20))
    pin = _block((-1, -1, 0), (1, 1, 30))
    meshes = [base, sleeve, pin]
    payload = motion_payload([0, 1, 2], [[], [0.0, 0.0, 1.0], [0.0, 0.0, 1.0]], meshes)
    sleeve_top = 20 + _travel_mm(payload, 1, meshes)
    assert 0 + _travel_mm(payload, 2, meshes) > sleeve_top


def test_a_part_clears_where_an_earlier_part_rests_when_that_part_leaves_the_other_way():
    # The sleeve comes off downward after the pin comes off upward. While the pin
    # leaves, the sleeve is still where it rests; cleared only from where the
    # sleeve ends up below, the pin would stop inside it.
    from cadless.assembly_guide import motion_payload

    bracket = _block((-20, -20, 0), (20, 20, 10))
    sleeve = _block((-4, -4, -30), (4, 4, 70))
    pin = _block((-1, -1, -40), (1, 1, 80))
    meshes = [bracket, sleeve, pin]
    payload = motion_payload([0, 1, 2], [[], [0.0, 0.0, -1.0], [0.0, 0.0, 1.0]], meshes)
    assert -40 + _travel_mm(payload, 2, meshes) > 70


def test_a_part_touching_another_from_the_side_counts_it_as_in_the_way():
    # Seen down the heading, the lid's edge meets the wall's face. Touching is
    # taken as meeting, so the lid is sent out past the top of the wall rather
    # than trusted to slide up its face.
    from cadless.assembly_guide import motion_payload

    meshes = [
        _block((0, 0, 0), (12, 10, 2)),
        _block((10, 0, 0), (12, 10, 20)),
        _block((0, 0, 2), (10, 10, 4)),
    ]
    payload = motion_payload([0, 1, 2], [[], [], [0.0, 0.0, 1.0]], meshes)
    assert 2 + _travel_mm(payload, 2, meshes) > 20


def test_a_part_off_the_path_of_a_part_leaving_along_x_does_not_push_it():
    # The same off-the-path rule for a heading along X, which needs a second
    # axis other than X to see the parts down it.
    from cadless.assembly_guide import motion_payload

    meshes = [
        _block((0, 0, 0), (10, 10, 10)),
        _block((0, 30, 0), (50, 35, 10)),
        _block((10, 0, 0), (12, 10, 10)),
    ]
    payload = motion_payload([0, 1, 2], [[], [], [1.0, 0.0, 0.0]], meshes)
    assert payload["travel"] == pytest.approx([0.0, 0.0, 0.35])


def test_no_travel_is_written_when_the_meshes_do_not_match_the_parts():
    # The viewport falls back to the spacing alone; a travel measured from a
    # different set of meshes would move these parts by another model's joints.
    from cadless.assembly_guide import motion_payload

    payload = motion_payload([0, 1], [[], [0.0, 0.0, 1.0]], [_cube()])
    assert "travel" not in payload
    assert "travel" not in motion_payload([0, 1], [[], [0.0, 0.0, 1.0]])


def test_the_guide_draws_a_pin_out_by_the_travel_the_viewport_plays(monkeypatch):
    # The guide's drawing and the viewport agree on how far apart "apart" is:
    # the exploded frame puts the pin where the motion file sends it.
    from cadless import assembly_guide

    block = _block((-20, -20, 0), (20, 20, 10))
    pin = _block((-2, -2, -30), (2, 2, 60))
    order, releases = [0, 1], [[], [0.0, 0.0, 1.0]]
    drawn = []
    real = assembly_guide.compose

    def recording(meshes, offsets):
        drawn.append(offsets)
        return real(meshes, offsets)

    monkeypatch.setattr(assembly_guide, "compose", recording)
    assert len(assembly_guide.guide_frames([block, pin], order, releases)) == 1
    played = _travel_mm(
        assembly_guide.motion_payload(order, releases, [block, pin]), 1, [block, pin]
    )
    assert drawn[0][1] == pytest.approx([0.0, 0.0, played])


def test_a_part_with_no_triangles_is_in_no_ones_way():
    # An exported part that reads back empty has nothing to clear and blocks
    # nothing, so the other parts travel as they would without it, in the
    # motion and in the guide's drawing alike.
    from cadless.assembly_guide import motion_payload

    block = _block((-20, -20, 0), (20, 20, 10))
    pin = _block((-2, -2, -30), (2, 2, 60))
    empty = np.empty((0, 3, 3))
    releases = [[], [0.0, 0.0, 1.0], [0.0, 0.0, 1.0]]
    with_empty = motion_payload([0, 1, 2], releases, [block, empty, pin])["travel"]
    without = motion_payload([0, 1], [[], [0.0, 0.0, 1.0]], [block, pin])["travel"]
    assert with_empty[2] == pytest.approx(without[1])
    assert len(guide_frames([block, empty, pin], [0, 1, 2], releases)) == 1


def test_no_travel_is_written_when_the_meshes_measure_nothing():
    # No vertex at all, or one out at infinity, leaves nothing a travel could be
    # a fraction of. The order and headings still stand.
    from cadless.assembly_guide import motion_payload

    empty = np.empty((0, 3, 3))
    payload = motion_payload([0, 1], [[], [0.0, 0.0, 1.0]], [empty, empty])
    assert payload["order"] == [0, 1]
    assert "travel" not in payload
    far = _cube()
    far[0, 0] = [np.inf, 0.0, 0.0]
    assert "travel" not in motion_payload([0, 1], [[], [0.0, 0.0, 1.0]], [_cube(), far])


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


# --- the motion the viewport plays ----------------------------------------


def test_the_motion_has_a_heading_slot_for_every_part_even_with_none_measured():
    # An engine that measured the order but recorded no headings hands over an
    # empty list. The viewport reads one entry per part, so each part gets an
    # empty one -- no heading, stays put -- rather than the list running short.
    from cadless.assembly_guide import motion_payload

    assert motion_payload([1, 0], [])["releases"] == [[], []]


def test_each_heading_is_filed_under_its_part_not_its_place_in_the_order():
    # The viewport moves part i along releases[i]. Filed by position in the
    # order instead, an order that is not 0, 1, 2 would send each part off along
    # another part's heading -- and the identity order hides that completely.
    from cadless.assembly_guide import motion_payload

    releases = [[0.0, 0.0, 1.0], [], [1.0, 0.0, 0.0]]
    assert motion_payload([2, 0, 1], releases)["releases"] == releases
