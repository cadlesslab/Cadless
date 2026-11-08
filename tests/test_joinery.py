"""``split_and_join``: halves the assembly check accepts by construction.

A whole part split and joined is judged by the engine's own check -- the
measurement a build's parts get -- so a joint that looks right but jams,
overlaps or comes apart the wrong way fails here. The rest measure what the
helper promises of the joint itself: its clearance, its margin, and where it
may and may not stand.
"""

import math
import subprocess
import sys
from pathlib import Path

import pytest
from build123d import (
    Axis,
    Box,
    Compound,
    Cylinder,
    Edge,
    Location,
    Plane,
    Polygon,
    Pos,
    Rot,
    extrude,
)

from cadless.assembly_check import evaluate_assembly, measure_assembly
from cadless.joinery import JoineryError, script_namespace, split_and_join
from cadless.printer_profile import AssemblySpec, BuildVolume

pytestmark = pytest.mark.build123d

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


def _rod(radius: float = 6):
    return Rot(0, 90, 0) * Cylinder(radius, 300)


def _drafted_plate(degrees: float):
    """The plate, its top rising ``degrees`` from the seam along +X."""
    rise = 150 * math.tan(math.radians(degrees))
    wedge = Plane.XZ * Polygon((0, 5), (150, 5), (150, 5 + rise), align=None)
    return _plate() + extrude(wedge, amount=50, both=True)


def _tabs_across(first, depth: float = 0.05) -> list[tuple[float, float]]:
    """Each tab's span across the seam, along Y, just past it."""
    return _runs(first, (depth, -60, 0), (depth, 60, 0), axis=1)


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


def test_a_joint_that_would_leave_a_half_in_two_pieces_is_refused(monkeypatch):
    # The check guards what every joint does to the halves; no joint the helper
    # places today breaks a half, so one is handed in whose socket would.
    slab = Pos(50, 0, 0) * Box(2, 400, 400)
    monkeypatch.setattr(
        "cadless.joinery._dovetails", lambda *args: ([(Box(1, 1, 1), slab)], False, True)
    )

    with pytest.raises(JoineryError, match="the joint would leave a half"):
        split_and_join(_plate(), SEAM, Axis.Z, CLEARANCE)


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


def test_a_wider_clearance_lets_no_socket_groove_further_past_the_cut_face():
    # A rim 3 mm tall standing flush with the seam on the half ahead: past the
    # socket's own opening at any clearance, so cutting through it is refused.
    part = _plate() + Pos(10, 0, 6.5) * Box(20, 100, 3)

    with pytest.raises(JoineryError, match="beyond the cut face"):
        split_and_join(part, SEAM, Axis.Z, 2.0)


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


def test_two_joints_hold_the_seam_at_opposite_ends():
    first, _second = split_and_join(_plate(), SEAM, Axis.Z, CLEARANCE)

    centres = sorted((low + high) / 2 for low, high in _tabs_across(first))
    assert len(centres) == 2 and centres[0] < -25 and centres[1] > 25


def test_a_full_size_joint_further_in_is_taken_before_a_small_one_at_the_end():
    # A window 4 mm past the seam leaves the end of the face room for a small
    # dovetail only; a full-size one fits further in.
    plate = _plate() - Pos(17, 35, 0) * Box(26, 30, 12)
    first, _second = split_and_join(plate, SEAM, Axis.Z, CLEARANCE)

    widths = [high - low for low, high in _tabs_across(first)]
    assert widths and all(width == pytest.approx(20, abs=0.1) for width in widths)


def test_a_seam_whose_far_side_rises_a_little_is_still_joined():
    # Past the seam the plate's top rises half a degree, so each socket comes out
    # through a sliver of it: its opening, not something standing in its way.
    halves = split_and_join(_drafted_plate(0.5), SEAM, Axis.Z, CLEARANCE)

    assert evaluate_assembly(measure_assembly(list(halves)), SPEC).failures == []


def test_a_lone_peg_keeps_the_halves_from_turning_about_it():
    # A rod with a flat has room for one peg; a round one would let the half
    # ahead turn about it and carry the flat out of line.
    part = _rod() - Pos(0, 9, 0) * Box(400, 10, 20)
    first, second = split_and_join(part, SEAM, Axis.X, CLEARANCE)

    pegs = (first & BEYOND).solids()
    centre = pegs[0].center()
    turned = second.moved(Pos(centre) * Rot(8, 0, 0) * Pos(-centre))
    assert _shared(first, turned) > 1e-3


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


def test_a_rod_cut_across_its_length_is_pegged_and_passes_the_assembly_check():
    first, second = split_and_join(_rod(), SEAM, Axis.X, CLEARANCE)

    assert first.bounding_box().max.X > 1.0  # the peg stands past the seam
    assert evaluate_assembly(measure_assembly([first, second]), SPEC).failures == []


@pytest.mark.parametrize("part", [_rod(3), Box(300, 12, 6)], ids=["6 mm rod", "12 x 6 bar"])
def test_a_thin_rod_or_bar_cut_across_its_length_is_still_pegged(part):
    first, _second = split_and_join(part, SEAM, Axis.X, CLEARANCE)

    assert (first & BEYOND).solids()  # a peg stands past the seam


def test_a_peg_clears_its_socket_by_the_clearance_and_no_more():
    first, second = split_and_join(_rod(), SEAM, Axis.X, CLEARANCE)

    # Across the seam, along Y, against the round of the peg.
    assert _shared(first, second.moved(Location((0, 0.9 * CLEARANCE, 0)))) < 1e-6
    assert _shared(first, second.moved(Location((0, 1.3 * CLEARANCE, 0)))) > 1e-4


def test_a_peg_clears_its_socket_on_the_flat_side_too():
    first, second = split_and_join(_rod(), SEAM, Axis.X, CLEARANCE)

    # The flat faces +Z on this seam: moving the socket down brings its flat onto
    # the peg's, and nothing else is as close.
    assert _shared(first, second.moved(Location((0, 0, -0.9 * CLEARANCE)))) < 1e-6
    assert _shared(first, second.moved(Location((0, 0, -1.3 * CLEARANCE)))) > 1e-4


def test_a_peg_keeps_its_margin_off_a_hole_just_past_the_seam():
    # A cross hole 5 mm past the seam, where a full-length peg's socket would end.
    rod = _rod() - Pos(6, 0, 0) * Rot(90, 0, 0) * Cylinder(1, 20)
    _first, second = split_and_join(rod, SEAM, Axis.X, CLEARANCE)

    sockets = ((rod & BEYOND) - second).solids()
    clear = Pos(6, 0, 0) * Rot(90, 0, 0) * Cylinder(2, 20)  # the hole and its margin
    assert sockets and all(_shared(socket, clear) < 1e-6 for socket in sockets)


def test_a_joint_keeps_its_margin_off_a_slot_through_the_seam():
    # Where a joint stands on a plain plate; the slot reaches 15 mm either side.
    plate = _plate() - Pos(0, 35, 0) * Box(30, 20, 12)
    first, second = split_and_join(plate, SEAM, Axis.Z, CLEARANCE)

    features = [*(first & BEYOND).solids(), *((plate & BEYOND) - second).solids()]
    clear = Pos(0, 35, 0) * Box(32, 22, 14)  # the slot and its margin
    assert features and all(_shared(feature, clear) < 1e-6 for feature in features)


def test_a_socket_keeps_its_margin_off_a_window_just_past_the_seam():
    # The cut face is whole; the window opens 4 mm into the half ahead, inside the
    # depth a full-size dovetail reaches there.
    window = Pos(17, 35, 0) * Box(26, 30, 12)
    plate = _plate() - window
    first, second = split_and_join(plate, SEAM, Axis.Z, CLEARANCE)

    for feature in [*(first & BEYOND).solids(), *((plate & BEYOND) - second).solids()]:
        assert _shared(feature, Pos(17, 35, 0) * Box(28, 32, 14)) < 1e-6  # window and margin


def test_two_joints_never_stand_so_close_that_their_sockets_meet():
    # 40 mm across leaves room for one full-size dovetail and its margins, not two.
    first, _second = split_and_join(Box(300, 40, 10), SEAM, Axis.Z, CLEARANCE)

    widths = [tab.bounding_box().size.Y for tab in (first & BEYOND).solids()]
    assert len(widths) == 1 and widths[0] < 27  # one dovetail: 20 at its root, 26.4 at its tip


def test_importing_the_api_loads_neither_the_helpers_nor_build123d():
    # The helpers import build123d at the top, so only the execution child may
    # import them -- inside a function -- and the api process keeps the kernel out.
    probe = (
        "import sys, backend.main; "
        "print(sorted({'build123d', 'cadless.joinery'} & set(sys.modules)))"
    )
    root = Path(__file__).resolve().parents[1]
    done = subprocess.run(
        [sys.executable, "-c", probe], cwd=root, capture_output=True, text=True, check=True
    )
    assert done.stdout.strip().splitlines()[-1] == "[]"


def test_each_script_gets_a_namespace_of_its_own():
    first, second = script_namespace(), script_namespace()

    first["split_and_join"] = None  # what one script does to its namespace
    assert second["split_and_join"] is split_and_join


def test_the_halves_take_the_names_given():
    first, second = split_and_join(_plate(), SEAM, Axis.Z, CLEARANCE, names=("left", "right"))

    assert (first.label, second.label) == ("left", "right")


@pytest.mark.parametrize("direction", [(1, 1, 0), (0, 0.6, 0.8)])
def test_a_direction_that_is_not_an_axis_is_refused(direction):
    with pytest.raises(JoineryError, match="six axis directions"):
        split_and_join(_plate(), SEAM, direction, CLEARANCE)


@pytest.mark.parametrize(
    "part, says",
    [
        ("a box", "build123d shape"),
        (
            Compound([Box(300, 100, 10), Pos(0, 200, 0) * Box(300, 100, 10)]),
            "one solid at a time: got 2; pass each solid to it on its own",
        ),
    ],
)
def test_what_is_cut_must_be_one_solid(part, says):
    with pytest.raises(JoineryError, match=says):
        split_and_join(part, SEAM, Axis.Z, CLEARANCE)


def test_a_plane_given_as_anything_but_a_plane_is_refused():
    with pytest.raises(JoineryError, match="plane must be a Plane"):
        split_and_join(_plate(), (0, 0, 0), Axis.Z, CLEARANCE)


def test_an_axis_neither_in_the_plane_nor_along_its_normal_is_refused():
    tilted = SEAM.rotated((0, 30, 0))  # the normal swings from +X toward -Z

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


def test_a_rod_cut_across_but_given_an_axis_in_its_cut_is_told_to_take_the_normal():
    # The commonest refusal live: an axle cut across its length, given an axis in
    # the cut. Pegs fit its face, so the refusal names the axis that takes them.
    with pytest.raises(JoineryError, match=r"pegs fit this cut face.*insert along \+X"):
        split_and_join(_rod(), SEAM, Axis.Z, CLEARANCE)


def test_a_face_no_peg_fits_is_not_offered_pegs():
    with pytest.raises(JoineryError) as refused:
        split_and_join(_shell(1), SEAM, Axis.Z, CLEARANCE)

    assert "pegs" not in str(refused.value)


def test_a_shell_whose_dovetails_found_no_room_is_not_offered_pegs():
    # Windows just past the seam take the room every dovetail in the top needs, yet
    # a peg fits there. A shell given the normal leaves its parts no order to go
    # together in, so pegs are offered only where no dovetail had a place at all.
    shell = _shell(7)
    for y in range(-45, 50, 15):
        shell -= Pos(16, y, 40) * Box(29, 12, 30)

    with pytest.raises(JoineryError) as refused:
        split_and_join(shell, SEAM, Axis.Z, CLEARANCE)

    assert "pegs" not in str(refused.value)


@pytest.mark.parametrize("clearance", [-0.1, 2.5, float("nan"), True])
def test_a_clearance_outside_what_a_printer_holds_is_refused(clearance):
    with pytest.raises(JoineryError, match="clearance"):
        split_and_join(_plate(), SEAM, Axis.Z, clearance)
