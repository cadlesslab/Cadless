"""``split_and_join``: halves the assembly check accepts by construction.

Every passing case is judged by the engine's own check -- the measurement a
build's parts get -- rather than by facts about the joint's shape, so a joint
that looks right but jams, overlaps or comes apart the wrong way fails here.
"""

import math

import pytest
from build123d import Axis, Box, Cylinder, Edge, Location, Plane, Pos, Rot

from cadless.assembly_check import evaluate_assembly, measure_assembly
from cadless.joinery import JoineryError, script_namespace, split_and_join
from cadless.printer_profile import AssemblySpec, BuildVolume

CLEARANCE = 0.2
SPEC = AssemblySpec(volume=BuildVolume(210.0, 200.0, 195.0), clearance_mm=CLEARANCE)
#: x = 0, facing +X: the half on +X is the one the tabs reach into.
SEAM = Plane.YZ
#: x >= 0, where the tabs and the sockets are.
BEYOND = Pos(150, 0, 0) * Box(300, 400, 400)


def _plate():
    return Box(300, 100, 10)


def _shell(wall: float):
    """A 300 x 120 x 80 mm box, hollow, open at the bottom, ``wall`` thick."""
    inside = Box(300 - 2 * wall, 120 - 2 * wall, 80 - wall + 2)
    return Box(300, 120, 80) - Pos(0, 0, -(wall + 2) / 2) * inside


def _rod():
    return Rot(0, 90, 0) * Cylinder(6, 300)


def _shared(first, second) -> float:
    common = first & second  # None where the two do not meet at all
    return 0.0 if common is None else common.volume


def _runs(shape, start, end, axis: int) -> list[tuple[float, float]]:
    """Where the line from ``start`` to ``end`` lies inside ``shape``, as
    ``(low, high)`` along coordinate ``axis`` (0, 1, 2 for x, y, z)."""
    hit = shape & Edge.make_line(start, end)
    spans = []
    for edge in hit.edges() if hit is not None else []:
        ends = [(v.X, v.Y, v.Z)[axis] for v in edge.vertices()]
        spans.append((min(ends), max(ends)))
    return sorted(spans)


def test_a_plate_cut_with_a_dovetail_passes_the_assembly_check():
    halves = split_and_join(_plate(), SEAM, Axis.Z, CLEARANCE)

    assert evaluate_assembly(measure_assembly(list(halves)), SPEC).failures == []


def test_each_half_of_a_cut_plate_is_one_solid():
    first, second = split_and_join(_plate(), SEAM, Axis.Z, CLEARANCE)

    assert (len(first.solids()), len(second.solids())) == (1, 1)


def test_the_halves_of_a_cut_plate_span_the_plate():
    first, second = split_and_join(_plate(), SEAM, Axis.Z, CLEARANCE)

    size = (first + second).bounding_box().size
    assert (size.X, size.Y, size.Z) == pytest.approx((300, 100, 10), abs=1e-6)


def test_the_socket_clears_the_tab_by_the_clearance_and_no_more():
    first, second = split_and_join(_plate(), SEAM, Axis.Z, CLEARANCE)

    # Across the seam, along Y, the flanks are 15 degrees off square: a shift a
    # little short of the clearance stays clear, one a little past it collides.
    assert _shared(first, second.moved(Location((0, 0.9 * CLEARANCE, 0)))) < 1e-6
    assert _shared(first, second.moved(Location((0, 1.3 * CLEARANCE, 0)))) > 1e-3


@pytest.mark.parametrize("depth", [0.05, 11.9])  # just past the seam; just short of the tip
def test_each_socket_flank_clears_its_tab_by_the_clearance_at_every_depth(depth):
    first, second = split_and_join(_plate(), SEAM, Axis.Z, CLEARANCE)

    across = ((depth, -60, 0), (depth, 60, 0))
    tabs = _runs(first, *across, axis=1)
    body = _runs(second, *across, axis=1)
    sockets = [(body[i][1], body[i + 1][0]) for i in range(len(body) - 1)]
    flank = CLEARANCE / math.cos(math.radians(15))  # the clearance, square to a 15-degree flank
    assert len(tabs) == len(sockets) == 2
    for (tab_low, tab_high), (socket_low, socket_high) in zip(tabs, sockets, strict=True):
        gaps = (tab_low - socket_low, socket_high - tab_high)
        assert gaps == pytest.approx((flank, flank), abs=0.01)


def test_the_tip_of_a_dovetail_clears_the_end_of_its_socket_by_the_clearance():
    first, second = split_and_join(_plate(), SEAM, Axis.Z, CLEARANCE)

    low, high = _runs(first, (0.05, -60, 0), (0.05, 60, 0), axis=1)[0]
    along = ((-1, (low + high) / 2, 0), (150, (low + high) / 2, 0))  # out through the tab
    tip = _runs(first, *along, axis=0)[-1][1]
    socket_end = _runs(second, *along, axis=0)[0][0]
    assert socket_end - tip == pytest.approx(CLEARANCE, abs=1e-3)


def test_a_socket_never_cuts_the_part_beyond_the_face_it_stands_on():
    # A wall inside the shell, 5 mm past the seam. A socket in the shell's top
    # runs out along Z, past the face its tab stands on, down through the wall.
    wall = Pos(6.5, 0, -3) * Box(3, 108, 74)
    part = _shell(6) + wall

    _first, second = split_and_join(part, SEAM, Axis.Z, CLEARANCE)

    assert _shared(second, wall) == pytest.approx(_shared(part, wall), rel=1e-6)


def test_a_seam_whose_every_joint_would_cut_through_the_part_is_refused():
    # A block on the half ahead, flush with the seam: a dovetail along Z, of any
    # size, would have to run out through it.
    part = _plate() + Pos(50, 0, 30) * Box(100, 100, 50)

    with pytest.raises(JoineryError, match="beyond the cut face"):
        split_and_join(part, SEAM, Axis.Z, CLEARANCE)


def test_a_face_flush_with_the_cut_on_one_half_only_takes_no_joint():
    # A block on the half behind, its face flush with the cut: material on one
    # side of the plane only, so a tab standing there would stand in the air.
    part = _plate() + Pos(-50, 0, 30) * Box(100, 100, 50)

    first, _second = split_and_join(part, SEAM, Axis.Z, CLEARANCE)

    tabs = [tab.bounding_box() for tab in (first & BEYOND).solids()]
    assert tabs and all(box.min.Z >= -5 - 1e-6 and box.max.Z <= 5 + 1e-6 for box in tabs)


def test_a_seam_whose_ends_are_taken_by_windows_is_joined_between_them():
    # Windows 1.5 mm past the seam near both ends; the middle of the face is solid.
    windows = [Pos(16, y, 0) * Box(29, 24, 12) for y in (34, -34)]
    plate = _plate() - windows[0] - windows[1]

    first, second = split_and_join(plate, SEAM, Axis.Z, CLEARANCE)

    features = [*(first & BEYOND).solids(), *((plate & BEYOND) - second).solids()]
    clear = [Pos(16, y, 0) * Box(31, 26, 14) for y in (34, -34)]  # each window and its margin
    assert features
    assert all(_shared(feature, window) < 1e-6 for feature in features for window in clear)


def test_the_halves_of_a_cut_plate_come_apart_along_the_axis_given():
    measured = measure_assembly(list(split_and_join(_plate(), SEAM, Axis.Z, CLEARANCE)))

    taken = [direction for direction in measured.releases if direction]
    assert taken and all(abs(abs(direction[2]) - 1) < 1e-6 for direction in taken)


def test_a_hollow_shell_cut_along_z_passes_the_assembly_check():
    halves = split_and_join(_shell(6), SEAM, Axis.Z, CLEARANCE)

    assert evaluate_assembly(measure_assembly(list(halves)), SPEC).failures == []


def test_no_joint_reaches_into_the_hollow_of_a_shell():
    first, second = split_and_join(_shell(6), SEAM, Axis.Z, CLEARANCE)

    hollow = Pos(0, 0, -4) * Box(288, 108, 76)
    assert _shared(first, hollow) + _shared(second, hollow) < 1e-6


def test_a_rod_cut_along_its_length_is_pegged_and_passes_the_assembly_check():
    first, second = split_and_join(_rod(), SEAM, Axis.X, CLEARANCE)

    assert first.bounding_box().max.X > 1.0  # the peg stands past the seam
    assert evaluate_assembly(measure_assembly([first, second]), SPEC).failures == []


def test_a_joint_keeps_its_margin_off_a_hole_on_the_seam():
    plate = _plate() - Cylinder(10, 12)
    first, second = split_and_join(plate, SEAM, Axis.Z, CLEARANCE)

    beyond = Pos(150, 0, 0) * Box(300, 200, 20)  # x >= 0, where tabs and sockets are
    tabs = (first & beyond).solids()
    sockets = ((plate & beyond) - second).solids()
    assert tabs and sockets
    for feature in [*tabs, *sockets]:
        box = feature.bounding_box()
        assert box.min.Y >= 11.0 or box.max.Y <= -11.0  # hole radius 10, margin 1


def test_a_socket_keeps_its_margin_off_a_window_just_past_the_seam():
    # The cut face is whole; the window opens 4 mm into the half ahead, inside the
    # depth a full-size dovetail reaches there.
    window = Pos(17, 35, 0) * Box(26, 30, 12)
    plate = _plate() - window
    first, second = split_and_join(plate, SEAM, Axis.Z, CLEARANCE)

    beyond = Pos(150, 0, 0) * Box(300, 200, 20)
    for feature in [*(first & beyond).solids(), *((plate & beyond) - second).solids()]:
        assert _shared(feature, Pos(17, 35, 0) * Box(28, 32, 14)) < 1e-6  # window and margin


def test_each_script_gets_a_namespace_of_its_own():
    first, second = script_namespace(), script_namespace()

    first["split_and_join"] = None  # what one script does to its namespace
    assert second["split_and_join"] is split_and_join


def test_the_halves_take_the_names_given():
    first, second = split_and_join(_plate(), SEAM, Axis.Z, CLEARANCE, names=("left", "right"))

    assert (first.label, second.label) == ("left", "right")


@pytest.mark.parametrize("direction", [(1, 1, 0), (0, 0.6, 0.8)])
def test_a_direction_that_is_not_an_axis_is_refused(direction):
    with pytest.raises(JoineryError, match="axis"):
        split_and_join(_plate(), SEAM, direction, CLEARANCE)


def test_an_axis_neither_in_the_plane_nor_along_its_normal_is_refused():
    tilted = SEAM.rotated((0, 30, 0))  # the normal swings from +X toward Y

    with pytest.raises(JoineryError, match="normal"):
        split_and_join(_plate(), tilted, Axis.X, CLEARANCE)


def test_a_cut_that_leaves_a_half_in_two_pieces_is_refused():
    fork = Pos(0, -25, 0) * Box(100, 10, 10) + Pos(-40, 0, 0) * Box(20, 60, 10)
    fork += Pos(40, 0, 0) * Box(20, 60, 10)

    with pytest.raises(JoineryError, match="pieces"):
        split_and_join(fork, Plane.XZ, Axis.X, CLEARANCE)


def test_a_wall_too_thin_for_a_joint_is_refused():
    with pytest.raises(JoineryError, match="thicker"):
        split_and_join(_shell(1), SEAM, Axis.Z, CLEARANCE)


@pytest.mark.parametrize("clearance", [-0.1, 2.5, float("nan")])
def test_a_clearance_outside_what_a_printer_holds_is_refused(clearance):
    with pytest.raises(JoineryError, match="clearance"):
        split_and_join(_plate(), SEAM, Axis.Z, clearance)
