"""``split_and_join``: halves the assembly check accepts by construction.

Every passing case is judged by the engine's own check -- the measurement a
build's parts get -- rather than by facts about the joint's shape, so a joint
that looks right but jams, overlaps or comes apart the wrong way fails here.
"""

import pytest
from build123d import Axis, Box, Cylinder, Location, Plane, Pos, Rot

from cadless.assembly_check import evaluate_assembly, measure_assembly
from cadless.joinery import JoineryError, split_and_join
from cadless.printer_profile import AssemblySpec, BuildVolume

CLEARANCE = 0.2
SPEC = AssemblySpec(volume=BuildVolume(210.0, 200.0, 195.0), clearance_mm=CLEARANCE)
#: x = 0, facing +X: the half on +X is the one the tabs reach into.
SEAM = Plane.YZ


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
