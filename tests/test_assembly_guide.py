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


def test_a_part_clears_where_an_earlier_part_ends_up_across_its_path():
    # A tall post beside the cap slides out sideways after the cap is lifted off,
    # and ends up standing where the cap's way out runs. At rest it is in no way
    # of the cap; out, it is, so the cap goes up past its top.
    from cadless.assembly_guide import motion_payload

    base = _block((-40, -10, -10), (40, 10, -1))
    post = _block((-30, -5, 0), (-20, 5, 40))
    cap = _block((-5, -5, 0), (5, 5, 10))
    meshes = [base, post, cap]
    payload = motion_payload([0, 1, 2], [[], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]], meshes)
    assert 0 + _travel_mm(payload, 2, meshes) > 40


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


def test_a_heading_with_no_finite_direction_does_not_stop_the_measuring():
    # An infinite heading gives no direction to see the parts along; that part
    # keeps a spacing, and the others are measured as beside a part standing
    # still.
    from cadless.assembly_guide import motion_payload

    block = _block((-20, -20, 0), (20, 20, 10))
    side = _block((30, 30, 0), (40, 40, 10))
    pin = _block((-2, -2, -30), (2, 2, 60))
    releases = [[], [float("inf"), 0.0, 0.0], [0.0, 0.0, 1.0]]
    travel = motion_payload([0, 1, 2], releases, [block, side, pin])["travel"]
    still = motion_payload([0, 1, 2], [[], [], [0.0, 0.0, 1.0]], [block, side, pin])["travel"]
    assert all(np.isfinite(travel))
    assert travel[1] > 0
    assert travel[2] == pytest.approx(still[2])


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


# --- how far the parts spread, all of them out ----------------------------


def _beam(pieces: int):
    """A beam split end to end into ``pieces``, the first of them at the right
    end staying put and each of the others slid on along +X after the one to its
    right, so every one of them comes off along -X, the leftmost first. Each
    piece has to clear the one to its right where that one ends up, so the
    spacing adds up along the beam."""
    meshes = [_block((-10 * (i + 1), 0, 0), (-10 * i, 5, 5)) for i in range(pieces)]
    order = list(range(pieces))
    releases = [[]] + [[-1.0, 0.0, 0.0]] * (pieces - 1)
    return meshes, order, releases


def _spread(payload: dict, meshes, releases) -> float:
    """The parts fully out, each moved its travel along its heading: their
    extent as a multiple of the assembled model's largest dimension."""
    points = [m.reshape(-1, 3) for m in meshes]
    whole = np.concatenate(points)
    size = float((whole.max(axis=0) - whole.min(axis=0)).max())
    out = [
        p + np.asarray(r or [0.0, 0.0, 0.0]) * payload["travel"][i] * size
        for i, (p, r) in enumerate(zip(points, releases, strict=True))
    ]
    spread = np.concatenate(out)
    return float((spread.max(axis=0) - spread.min(axis=0)).max()) / size


def _gaps(travel: list[float]) -> list[float]:
    """How much further each piece of the beam goes than the one to its right."""
    return [travel[i + 1] - travel[i] for i in range(len(travel) - 1)]


def test_a_long_chain_spreads_as_far_as_two_and_a_half_times_the_model_and_no_further():
    # Twenty pieces leaving one after another the same way: each piece's spacing
    # is added onto the one before, which at the usual spacing spreads the beam
    # well past the bound. Cut to fit, it fills the bound rather than falling
    # short of it, so the spacing kept is the most that fits.
    from cadless.assembly_guide import motion_payload

    meshes, order, releases = _beam(20)
    assert 2.4 < _spread(motion_payload(order, releases, meshes), meshes, releases) <= 2.5 + 1e-6


def test_a_chain_too_long_to_fit_keeps_the_least_gap_at_every_joint():
    # A hundred pieces cannot be spread within the bound with a gap a reader can
    # see at every joint, and the bound is what gives: every joint still opens by
    # the least spacing, and the beam spreads further than the bound.
    from cadless.assembly_guide import motion_payload

    meshes, order, releases = _beam(100)
    payload = motion_payload(order, releases, meshes)
    assert min(_gaps(payload["travel"])) == pytest.approx(0.02)


def test_the_guide_draws_a_long_chain_by_the_travel_the_viewport_plays(monkeypatch):
    # Cut to fit, the travel is still one travel: the guide's drawings move the
    # pieces exactly as far as the motion file sends them.
    from cadless import assembly_guide

    meshes, order, releases = _beam(20)
    drawn = []
    real = assembly_guide._frame_offsets

    def recording(shown, releases_, moving, travels):
        drawn.append(list(travels))
        return real(shown, releases_, moving, travels)

    monkeypatch.setattr(assembly_guide, "_frame_offsets", recording)
    assert assembly_guide.guide_frames(meshes, order, releases)
    played = assembly_guide.motion_payload(order, releases, meshes)["travel"]
    assert drawn[-1] == pytest.approx([travel * 200.0 for travel in played])


def test_a_sub_assembly_comes_apart_within_two_and_a_half_times_its_own_size():
    # The bound is the parts measured together: off the model, a sub-assembly's
    # pieces coming off one another are held to the sub-assembly's own size, not
    # to the room a much longer base leaves.
    from cadless.assembly_guide import motion_payload

    beam, _, _ = _beam(8)
    meshes = [_block((-150, -20, -10), (150, 20, 0)), *beam]
    splits = [[]] + [[-1.0, 0.0, 0.0]] * 7
    payload = motion_payload(
        list(range(9)),
        [[]] + [[0.0, 0.0, 1.0]] * 8,
        meshes,
        steps=[[0], list(range(1, 9))],
        subassemblies=[{"parts": list(range(1, 9)), "splits": splits}],
    )
    split_travel = payload["subassemblies"][0]["split_travel"]
    out = [
        piece.reshape(-1, 3) + np.asarray(split or [0.0, 0.0, 0.0]) * travel * 300.0
        for piece, split, travel in zip(beam, splits, split_travel, strict=True)
    ]
    every = np.concatenate(out)
    assert 2.4 < float((every.max(axis=0) - every.min(axis=0)).max()) / 80.0 <= 2.5 + 1e-6


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


# --- a grouped motion -----------------------------------------------------

# A block that stays, a sleeve that sits on it, and a pin down through the sleeve
# and into the block. The sleeve and the pin are put together off the model and
# lowered onto it as one piece; off it, the pin is drawn up out of the sleeve.
_SLEEVE_ORDER = [0, 1, 2]
_SLEEVE_RELEASES = [[], [0.0, 0.0, 1.0], [0.0, 0.0, 1.0]]
_SLEEVE_STEPS = [[0], [1, 2]]
_PINNED = [{"parts": [1, 2], "splits": [[], [0.0, 0.0, 1.0]]}]


def _sleeve_meshes() -> list[np.ndarray]:
    return [
        _block((-20, -20, 0), (20, 20, 10)),
        _block((-4, -4, 10), (4, 4, 20)),
        _block((-1, -1, -30), (1, 1, 20)),
    ]


def test_runs_are_written_as_given_and_need_no_meshes():
    from cadless.assembly_guide import motion_payload

    payload = motion_payload(_SLEEVE_ORDER, _SLEEVE_RELEASES, steps=_SLEEVE_STEPS)
    assert payload["steps"] == [[0], [1, 2]]


def test_a_sub_assembly_is_written_with_its_members_and_their_splits():
    from cadless.assembly_guide import motion_payload

    payload = motion_payload(
        _SLEEVE_ORDER,
        _SLEEVE_RELEASES,
        _sleeve_meshes(),
        steps=_SLEEVE_STEPS,
        subassemblies=_PINNED,
    )
    (written,) = payload["subassemblies"]
    assert (written["parts"], written["splits"]) == ([1, 2], [[], [0.0, 0.0, 1.0]])


def test_a_member_that_stays_with_the_sub_assembly_goes_nowhere_off_it():
    from cadless.assembly_guide import motion_payload

    meshes = _sleeve_meshes()
    payload = motion_payload(
        _SLEEVE_ORDER, _SLEEVE_RELEASES, meshes, steps=_SLEEVE_STEPS, subassemblies=_PINNED
    )
    assert payload["subassemblies"][0]["split_travel"][0] == 0


def test_a_member_that_splits_off_is_drawn_clear_of_the_member_it_runs_through():
    # The pin is held far deeper in the sleeve than a spacing, so only a travel
    # measured from the two of them brings it all the way out.
    from cadless.assembly_guide import motion_payload

    meshes = _sleeve_meshes()
    payload = motion_payload(
        _SLEEVE_ORDER, _SLEEVE_RELEASES, meshes, steps=_SLEEVE_STEPS, subassemblies=_PINNED
    )
    pin = payload["subassemblies"][0]["split_travel"][1]
    points = np.concatenate([m.reshape(-1, 3) for m in meshes])
    size = float((points.max(axis=0) - points.min(axis=0)).max())
    assert -30 + pin * size > 20


def test_a_sub_assembly_without_the_parts_meshes_is_refused():
    # How far a member comes off is measured, and there is nothing to measure.
    from cadless.assembly_guide import motion_payload

    with pytest.raises(ValueError, match="mesh"):
        motion_payload(_SLEEVE_ORDER, _SLEEVE_RELEASES, steps=_SLEEVE_STEPS, subassemblies=_PINNED)


def test_a_sub_assembly_whose_meshes_measure_nothing_is_refused():
    from cadless.assembly_guide import motion_payload

    empty = np.empty((0, 3, 3))
    with pytest.raises(ValueError, match="size"):
        motion_payload(
            _SLEEVE_ORDER,
            _SLEEVE_RELEASES,
            [empty, empty, empty],
            steps=_SLEEVE_STEPS,
            subassemblies=_PINNED,
        )


def test_a_grouping_the_viewport_would_not_play_is_raised_rather_than_returned():
    from cadless.assembly_guide import motion_payload

    with pytest.raises(ValueError, match="steps do not add up to the order"):
        motion_payload(
            _SLEEVE_ORDER,
            _SLEEVE_RELEASES,
            _sleeve_meshes(),
            steps=[[0], [2, 1]],
            subassemblies=_PINNED,
        )


def test_an_order_naming_a_part_twice_is_raised_on_the_grouped_path():
    # Counted as the parts the order describes, one per entry, the way the
    # viewport counts the parts it draws -- not as the parts the order names,
    # which would let a part named twice stand in for one named never.
    from cadless.assembly_guide import motion_payload

    with pytest.raises(ValueError, match="exactly once"):
        motion_payload([0, 1, 1], _SLEEVE_RELEASES, steps=[[0], [1, 1]])


def test_members_listed_against_the_order_are_measured_in_the_order_they_go_together():
    # The pin is listed before the sleeve it runs through; it still went in
    # after it, so it still has the sleeve to clear on its way out.
    from cadless.assembly_guide import motion_payload

    meshes = _sleeve_meshes()
    payload = motion_payload(
        _SLEEVE_ORDER,
        _SLEEVE_RELEASES,
        meshes,
        steps=_SLEEVE_STEPS,
        subassemblies=[{"parts": [2, 1], "splits": [[0.0, 0.0, 1.0], []]}],
    )
    pin = payload["subassemblies"][0]["split_travel"][0]
    points = np.concatenate([m.reshape(-1, 3) for m in meshes])
    size = float((points.max(axis=0) - points.min(axis=0)).max())
    assert -30 + pin * size > 20


def test_a_run_entry_that_is_not_a_whole_number_is_named_rather_than_rounded():
    # Rounded, 1.7 would become part 1: a grouping the caller never wrote.
    from cadless.assembly_guide import motion_payload

    with pytest.raises(ValueError, match="run 1 is not a non-empty list of whole numbers"):
        motion_payload(_SLEEVE_ORDER, _SLEEVE_RELEASES, steps=[[0], [1.7, 2]])


def test_a_member_that_is_not_a_whole_number_is_named_rather_than_rounded():
    from cadless.assembly_guide import motion_payload

    with pytest.raises(ValueError, match="parts are not two or more different whole numbers"):
        motion_payload(
            _SLEEVE_ORDER,
            _SLEEVE_RELEASES,
            _sleeve_meshes(),
            steps=_SLEEVE_STEPS,
            subassemblies=[{"parts": [1.7, 2], "splits": [[], [0.0, 0.0, 1.0]]}],
        )


def test_a_grouping_built_from_arrays_is_written_as_numbers():
    # A caller that cut its order with numpy hands over arrays, not lists.
    from cadless.assembly_guide import motion_payload

    payload = motion_payload(
        _SLEEVE_ORDER,
        _SLEEVE_RELEASES,
        _sleeve_meshes(),
        steps=np.split(np.array(_SLEEVE_ORDER), [1]),
        subassemblies=[{"parts": np.array([1, 2]), "splits": [[], np.array([0.0, 0.0, 1.0])]}],
    )
    assert payload["steps"] == [[0], [1, 2]]
    assert payload["subassemblies"][0]["splits"] == [[], [0.0, 0.0, 1.0]]


def test_members_held_as_single_precision_floats_are_whole_numbers_still():
    from cadless.assembly_guide import motion_payload

    payload = motion_payload(
        _SLEEVE_ORDER,
        _SLEEVE_RELEASES,
        _sleeve_meshes(),
        steps=_SLEEVE_STEPS,
        subassemblies=[
            {"parts": np.array([1, 2], dtype=np.float32), "splits": [[], [0.0, 0.0, 1.0]]}
        ],
    )
    assert payload["subassemblies"][0]["parts"] == [1, 2]


def test_members_given_as_a_set_are_refused_rather_than_paired_by_chance():
    # A set has no order, and a member is paired with its split by position.
    from cadless.assembly_guide import motion_payload

    with pytest.raises(ValueError, match="sub-assembly 0 is not a mapping with a list of parts"):
        motion_payload(
            _SLEEVE_ORDER,
            _SLEEVE_RELEASES,
            _sleeve_meshes(),
            steps=_SLEEVE_STEPS,
            subassemblies=[{"parts": {1, 2}, "splits": [[], [0.0, 0.0, 1.0]]}],
        )


def test_a_sub_assembly_with_no_splits_is_refused_as_a_grouping():
    from cadless.assembly_guide import motion_payload

    with pytest.raises(ValueError, match="sub-assembly 0 is not a mapping"):
        motion_payload(
            _SLEEVE_ORDER,
            _SLEEVE_RELEASES,
            _sleeve_meshes(),
            steps=_SLEEVE_STEPS,
            subassemblies=[{"parts": [1, 2]}],
        )
