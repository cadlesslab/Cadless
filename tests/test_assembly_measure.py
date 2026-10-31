"""Assembly measurement tests. Marked build123d: these execute the OCCT kernel.

The policy that reads these numbers is tested in ``tests/test_assembly_check.py``
against synthetic values; here the numbers themselves come from real solids.
"""

import json
import math
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from build123d import (
    Align,
    Box,
    Cone,
    Cylinder,
    GeomType,
    Pos,
    Rot,
    Sphere,
    Torus,
    Vector,
)

from cadless.assembly_check import _BLOCKED as BLOCKED
from cadless.assembly_check import _FREE as FREE
from cadless.assembly_check import _UNDECIDED as UNDECIDED
from cadless.assembly_check import evaluate_assembly, measure_assembly
from cadless.printer_profile import AssemblySpec, BuildVolume

pytestmark = pytest.mark.build123d

CLEARANCE = 0.2


def _mated_pair():
    """Two blocks sharing a face, separated by the joint clearance."""
    return [Box(20, 20, 10), Pos(20 + CLEARANCE, 0, 0) * Box(20, 20, 10)]


def _caged(wall=4.0):
    """A block sealed inside a hollow shell -- assemblable in no order.

    Two disjoint solids with the joint clearance between them, so what stops the
    search is enclosure and nothing else. Two earlier versions were wrong in
    opposite ways and both are worth naming, because each passed the trapping
    assertions for a reason that had nothing to do with caging: six
    interpenetrating plates blocked every part at rest, so any interfering pile
    would have passed; six *disjoint* plates are not a cage at all, because each
    plate simply slides away on its own and frees the block behind it.
    """
    inner = 6.0
    cavity = inner + 2 * CLEARANCE
    shell = Box(cavity + 2 * wall, cavity + 2 * wall, cavity + 2 * wall) - Box(
        cavity, cavity, cavity
    )
    return [Box(inner, inner, inner), shell]


def _gap_between(measurements, first, second):
    for i, j, distance in measurements.gaps:
        if {i, j} == {first, second}:
            return distance
    raise AssertionError(f"no gap recorded for {first} and {second}")


# --- the boxes ------------------------------------------------------------


def test_each_part_reports_its_own_box_not_the_union():
    m = measure_assembly(_mated_pair())
    assert len(m.part_bboxes) == 2
    for box in m.part_bboxes:
        assert box == pytest.approx([20.0, 20.0, 10.0], rel=1e-6)


# --- overlap --------------------------------------------------------------


def test_a_correctly_cleared_joint_records_no_overlap():
    m = measure_assembly(_mated_pair())
    assert m.overlaps == []


def test_touching_parts_record_no_overlap():
    m = measure_assembly([Box(20, 20, 10), Pos(20, 0, 0) * Box(20, 20, 10)])
    assert m.overlaps == []


def test_interpenetrating_parts_record_the_shared_volume():
    # 1 mm of a 20 x 10 face -> 200 mm^3.
    m = measure_assembly([Box(20, 20, 10), Pos(19, 0, 0) * Box(20, 20, 10)])
    assert len(m.overlaps) == 1
    first, second, shared = m.overlaps[0]
    assert {first, second} == {0, 1}
    assert shared == pytest.approx(200.0, rel=1e-3)


# --- gaps -----------------------------------------------------------------


def test_the_gap_of_a_cleared_joint_is_the_clearance():
    m = measure_assembly(_mated_pair())
    assert _gap_between(m, 0, 1) == pytest.approx(CLEARANCE, abs=1e-6)


def test_a_distant_part_records_its_real_distance():
    m = measure_assembly([Box(10, 10, 10), Pos(60, 0, 0) * Box(10, 10, 10)])
    assert _gap_between(m, 0, 1) == pytest.approx(50.0, abs=1e-6)


def test_every_pair_appears_in_the_gap_table():
    m = measure_assembly(
        [Box(10, 10, 10), Pos(20, 0, 0) * Box(10, 10, 10), Pos(40, 0, 0) * Box(10, 10, 10)]
    )
    pairs = {frozenset((int(i), int(j))) for i, j, _ in m.gaps}
    assert pairs == {frozenset((0, 1)), frozenset((0, 2)), frozenset((1, 2))}


# --- the assembly order ---------------------------------------------------


def test_a_two_part_split_has_an_order():
    m = measure_assembly(_mated_pair())
    assert m.order is not None
    assert sorted(m.order) == [0, 1]
    assert m.trapped == []


def test_a_chain_of_four_has_an_order():
    chain = [Pos(i * (20 + CLEARANCE), 0, 0) * Box(20, 20, 10) for i in range(4)]
    m = measure_assembly(chain)
    assert m.order is not None
    assert sorted(m.order) == [0, 1, 2, 3]


def test_the_cage_fixture_holds_its_block_without_touching_it():
    # The trapping tests below are only about caging if the fixture is disjoint.
    # Asserted here rather than assumed, because an interpenetrating pile blocks
    # every part at rest and would pass them for the wrong reason.
    m = measure_assembly(_caged())
    assert m.overlaps == []


def test_a_caged_part_has_no_order_and_is_named():
    m = measure_assembly(_caged())
    assert m.order is None
    assert 0 in m.trapped


def test_a_blocker_thinner_than_a_coarse_step_is_not_jumped():
    # The failure this guards: a search that advances by a fixed step clears a
    # thin wall on both sides without ever sampling inside it, and reports a
    # sealed-in part as free. This shell is 0.4 mm thick against tens of
    # millimetres of travel, so only a search that rules the whole path catches
    # it -- sweeping it, or walking it by the measured separation.
    m = measure_assembly(_caged(wall=0.4))
    assert m.order is None
    assert 0 in m.trapped


def test_an_interlocking_joint_slides_apart_rather_than_pulling_apart():
    # A tab in a socket: the order search must find the slide axis. If it only
    # ever tried the face normal it would call this trapped.
    tab = Box(20, 20, 10) + Pos(12, 0, 0) * Box(6, 12, 6)
    socket = (Pos(26, 0, 0) * Box(20, 20, 10)) - (Pos(12, 0, 0) * Box(6, 12.4, 6.4))
    m = measure_assembly([tab, socket])
    assert m.overlaps == []
    assert m.order is not None


def _captive_tongue(angle):
    """A tongue in a blind pocket: it can leave along one direction and no other.

    Rotated, that direction stops being an axis, which is the case the six axes
    alone cannot answer.
    """
    tongue = Box(20, 20, 20) + Pos(14, 0, 0) * Box(8, 10, 10)
    body = Pos(20, 0, 0) * Box(20, 20, 20)
    pocket = Pos(14, 0, 0) * Box(8 + CLEARANCE, 10 + CLEARANCE, 10 + CLEARANCE)
    return [Rot(0, 0, angle) * tongue, Rot(0, 0, angle) * (body - pocket)]


def test_a_joint_whose_only_exit_is_an_axis_has_an_order():
    m = measure_assembly(_captive_tongue(0))
    assert m.order is not None


def test_a_joint_whose_only_exit_is_not_an_axis_still_has_an_order():
    # Measured: at this rotation not one of the six axes is free, so a search
    # trying only those refuses a joint that comes apart perfectly well. The
    # directions taken from the geometry are what stop that false refusal, and
    # this is the fixture that shows it -- the unrotated case above passes either
    # way, so it cannot.
    m = measure_assembly(_captive_tongue(45))
    assert m.order is not None
    assert m.trapped == []


# --- the travel test itself -----------------------------------------------


def test_a_grazing_contact_is_not_stepped_over():
    """The clash this catches lasts 0.69 mm of travel and is not near either end.

    Measured on this exact pair: sliding the first cube along (1,1,0) clips the
    corner of the second between t=14.150 and t=14.840. A search advancing by a
    fixed fraction of a part's size samples 14.02 and then 19.02 and reports the
    part free -- the interval a grazing contact occupies has nothing to do with
    how thick either part is, so no fraction of a part bounds it. Ruling the whole
    path -- sweeping it, or walking it by the measured separation -- is what
    closes this.
    """
    from build123d import Vector

    import cadless.assembly_check as module

    first = Pos(5, 5, 5) * Box(10, 10, 10)
    second = Pos(25, 5.5, 5) * Box(10, 10, 10)
    parts = [first, second]
    spheres = [module._sphere(part) for part in parts]
    heading = Vector(1, 1, 0).normalized()
    budget = module._Budget(10**6, 600.0)

    verdict = module._rule_heading(
        parts, spheres, 0, [1], heading, module._look_ahead(parts), budget
    )
    assert verdict == BLOCKED, "the search walked past a real collision"


# --- failing closed -------------------------------------------------------


class _Awkward:
    """A part whose kernel operations raise, as a degenerate solid's can.

    Real enough for the measurement to get as far as calling them: it answers a
    bounding box and moves, and fails at exactly the two probes that matter.
    """

    def __init__(self, size=(10.0, 10.0, 10.0), centre=(0.0, 0.0, 0.0)):
        self._size = SimpleNamespace(X=size[0], Y=size[1], Z=size[2])
        self._centre = SimpleNamespace(X=centre[0], Y=centre[1], Z=centre[2])

    def bounding_box(self):
        return SimpleNamespace(size=self._size, center=lambda: self._centre)

    def intersect(self, other, include_touched=False):
        raise RuntimeError("OCCT boolean failed")

    def distance_to(self, other):
        raise RuntimeError("OCCT distance failed")

    def moved(self, location):
        return self


def test_an_overlap_probe_the_kernel_refuses_is_reported_not_assumed_away():
    m = measure_assembly([_Awkward(), _Awkward(centre=(30.0, 0.0, 0.0))])
    assert any("shared volume" in reason for reason in m.unchecked)


def test_a_distance_probe_the_kernel_refuses_is_reported_not_assumed_away():
    m = measure_assembly([_Awkward(), _Awkward(centre=(30.0, 0.0, 0.0))])
    assert any("gap" in reason for reason in m.unchecked)
    assert m.gaps == []


_SPEC = AssemblySpec(volume=BuildVolume(210.0, 200.0, 195.0), clearance_mm=CLEARANCE)


@pytest.fixture(params=[math.nan, math.inf], ids=["nan", "inf"])
def volumes_are_not_numbers(request, monkeypatch):
    """Every volume the kernel computes comes back as no measurement."""
    from OCP.GProp import GProp_GProps

    monkeypatch.setattr(GProp_GProps, "Mass", lambda self: request.param)


@pytest.fixture(params=[math.nan, math.inf], ids=["nan", "inf"])
def distances_are_not_numbers(request, monkeypatch):
    """Every distance the kernel computes comes back as no measurement."""
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape

    monkeypatch.setattr(BRepExtrema_DistShapeShape, "Value", lambda self: request.param)


def test_a_shared_volume_that_is_not_a_number_is_reported_not_dropped(volumes_are_not_numbers):
    m = measure_assembly([Box(20, 20, 10), Pos(19, 0, 0) * Box(20, 20, 10)])
    assert m.overlaps == []
    assert any(
        reason.startswith("shared volume between part 1 and part 2") for reason in m.unchecked
    )


def test_a_negative_shared_volume_is_no_answer(monkeypatch):
    # The kernel's volume is signed, and no intersection holds less than nothing.
    from OCP.GProp import GProp_GProps

    from cadless.assembly_check import _shared_volume

    monkeypatch.setattr(GProp_GProps, "Mass", lambda self: -5.0)
    assert _shared_volume(Box(20, 20, 10), Pos(19, 0, 0) * Box(20, 20, 10)) is None


def test_interpenetrating_parts_measured_as_no_number_are_given_no_order(monkeypatch):
    # NaN alone: an infinite volume ends with no order whether it is refused or
    # read as a clash, so it could not tell the two apart.
    from OCP.GProp import GProp_GProps

    monkeypatch.setattr(GProp_GProps, "Mass", lambda self: math.nan)
    m = measure_assembly([Box(20, 20, 10), Pos(19, 0, 0) * Box(20, 20, 10)])
    assert m.order is None


def test_a_distance_that_is_not_a_number_is_reported_not_recorded(distances_are_not_numbers):
    m = measure_assembly([Box(10, 10, 10), Pos(60, 0, 0) * Box(10, 10, 10)])
    assert m.gaps == []
    assert any(reason.startswith("gap between part 1 and part 2") for reason in m.unchecked)


def test_a_sound_joint_whose_distance_was_not_measured_is_not_called_loose(
    distances_are_not_numbers,
):
    report = evaluate_assembly(measure_assembly(_mated_pair()), _SPEC)
    assert report.failures == []


def test_parts_the_budget_left_unmeasured_are_not_called_loose():
    chain = [Box(20, 20, 10), Pos(20.2, 0, 0) * Box(20, 20, 10), Pos(40.4, 0, 0) * Box(20, 20, 10)]
    report = evaluate_assembly(measure_assembly(chain, probe_budget=2), _SPEC)
    assert report.failures == []


def test_a_prism_volume_that_is_not_a_number_is_no_answer(volumes_are_not_numbers):
    from cadless.assembly_check import _prism_overlap

    assert (
        _prism_overlap(Box(20, 20, 10).wrapped, (Pos(10, 0, 0) * Box(20, 20, 10)).wrapped) is None
    )


def test_a_cut_volume_that_is_not_a_number_is_no_answer(volumes_are_not_numbers):
    from cadless.assembly_check import _cut_volume

    assert _cut_volume(Box(20, 20, 10), Pos(10, 0, 0) * Box(20, 20, 10)) is None


def test_a_prism_whose_volume_is_not_a_number_is_no_sweep(volumes_are_not_numbers):
    # Its sign says which way it faces, and a volume that is not finite says
    # nothing a finite sweep could enclose.
    from OCP.gp import gp_Vec

    from cadless.assembly_check import _prism

    assert _prism(Box(10, 10, 10).faces()[0].wrapped, gp_Vec(0, 0, 5)) is None


def test_a_part_with_no_measurable_size_stops_the_order_search():
    m = measure_assembly([_Awkward(size=(0.0, 10.0, 10.0)), _Awkward(centre=(30.0, 0.0, 0.0))])
    assert m.order is None
    assert any("no measurable size" in reason for reason in m.unchecked)


_ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("position", ["nan", "inf", "1.7e308"])
def test_a_part_placed_at_no_number_is_measured_against_nothing(position):
    # Handed such a part, the kernel can stay inside one call past any budget, or
    # never come back, and a budget cannot interrupt a call already running. So
    # the measurement runs in an interpreter of its own here, where a hang fails
    # the test instead of holding the suite. The last position is a number, but
    # the middle of a box there is not.
    probe = (
        "import json, time\n"
        "from build123d import Box, Pos\n"
        "from cadless.assembly_check import measure_assembly\n"
        f"first = Pos(float({position!r}), 0, 0) * Box(20, 20, 10)\n"
        "start = time.monotonic()\n"
        "m = measure_assembly([first, Pos(30, 0, 0) * Box(20, 20, 10)], time_budget=5.0)\n"
        "print(json.dumps([time.monotonic() - start, m.unchecked]))\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    seconds, unchecked = json.loads(done.stdout)
    assert seconds < 5.0
    assert any(r.startswith("overlap and gap between every pair: part 1") for r in unchecked)
    assert "assembly order: a part has no measurable size" in unchecked


def test_parts_that_cannot_meet_along_a_direction_are_not_stepped_through():
    # Their bounding spheres miss each other entirely perpendicular to every
    # travel direction, so the search skips straight past without probing. The
    # skip is exact rather than an approximation, which is why it is allowed to
    # short-circuit at all.
    m = measure_assembly([Box(10, 10, 10), Pos(0, 500, 0) * Box(10, 10, 10)])
    assert m.order is not None
    assert m.trapped == []


def _as_nurbs(part):
    """The same solid with every face rewritten as a B-spline surface.

    No exact sweep is built for a B-spline face, so a pair made of these is the
    one the search still walks.
    """
    from build123d import Solid
    from OCP.BRepBuilderAPI import BRepBuilderAPI_NurbsConvert
    from OCP.TopAbs import TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    converted = BRepBuilderAPI_NurbsConvert(part.solid().wrapped, True).Shape()
    solid = Solid(TopoDS.Solid_s(TopExp_Explorer(converted, TopAbs_SOLID).Current()))
    assert {face.geom_type for face in solid.faces()} == {GeomType.BSPLINE}
    return solid


def test_a_distance_probe_that_cannot_run_is_reported_rather_than_cleared(monkeypatch):
    # Where the search walks, it advances by the measured separation, so the
    # distance query is what it rests on. One the kernel could not answer must not
    # read as "nothing in the way" -- that frees a part on no evidence -- nor as a
    # trap nobody measured, which sends a repair after geometry that may be fine.
    import cadless.assembly_check as module

    monkeypatch.setattr(module, "_distance", lambda first, second: None)
    m = measure_assembly([_as_nurbs(part) for part in _mated_pair()])
    assert m.order is None
    assert m.trapped == []
    assert any("could not measure part 1 and part 2" in reason for reason in m.unchecked)


def test_a_contact_the_walk_cannot_look_past_is_reported_rather_than_trapped(monkeypatch):
    # Blocks touching face to face, walked because neither can be swept: the
    # pairwise phase measures them where they stand, and every look the walk then
    # takes at the contact is refused. Nothing clashed, so nothing is trapped.
    import cadless.assembly_check as module

    real = module._shared_volume
    calls = []

    def first_only(first, second):
        calls.append(1)
        return real(first, second) if len(calls) == 1 else None

    monkeypatch.setattr(module, "_shared_volume", first_only)
    pair = [_as_nurbs(Box(20, 20, 10)), _as_nurbs(Pos(20, 0, 0) * Box(20, 20, 10))]
    m = measure_assembly(pair)
    assert m.order is None
    assert m.trapped == []
    assert any("could not measure part 1 and part 2" in reason for reason in m.unchecked)


def test_a_clash_probe_that_cannot_run_is_reported_rather_than_cleared(monkeypatch):
    # The other half: where two parts stand touching, the shared volume is what
    # says whether they clash, so a refused volume query is reported the same way.
    import cadless.assembly_check as module

    monkeypatch.setattr(module, "_shared_volume", lambda first, second: None)
    m = measure_assembly([Box(20, 20, 10), Pos(20, 0, 0) * Box(20, 20, 10)])
    assert m.order is None
    assert m.trapped == []
    assert any("could not measure part 1 and part 2" in reason for reason in m.unchecked)


def test_a_budget_spent_on_the_pairwise_phase_is_reported():
    # One budget covers the whole measurement, so a tiny one runs out on the very
    # first pair -- before the order search is ever reached.
    m = measure_assembly(_caged(), probe_budget=1)
    assert m.order is None
    assert any("pairs" in reason for reason in m.unchecked)


def test_a_budget_spent_on_the_order_search_is_reported_as_that():
    # Enough for the pairwise phase of the two-part fixture, which spends two
    # probes per pair, but not for the search that follows it.
    m = measure_assembly(_caged(), probe_budget=2 + 2)
    assert m.order is None
    assert any("order" in reason for reason in m.unchecked)


def test_a_spent_budget_does_not_masquerade_as_a_trapped_part():
    # "We ran out of budget" and "this part is walled in" are different answers
    # and the policy layer reports them differently, so they must not be conflated.
    assert measure_assembly(_caged(), probe_budget=1).trapped == []
    assert measure_assembly(_caged(), probe_budget=2 + 2).trapped == []


def test_a_spent_time_budget_stops_the_measurement():
    # The probe count bounds how many kernel calls run; the clock bounds how long
    # they take. A pathological solid can make one call slow, which counting
    # alone would never notice.
    m = measure_assembly(_caged(), time_budget=0.0)
    assert m.order is None
    assert m.unchecked


# --- degenerate inputs ----------------------------------------------------


def test_a_single_part_measures_no_relations():
    m = measure_assembly([Box(10, 10, 10)])
    assert m.part_bboxes == [pytest.approx([10.0, 10.0, 10.0], rel=1e-6)]
    assert m.overlaps == []
    assert m.gaps == []
    assert m.order == [0]


def test_no_parts_measures_nothing():
    m = measure_assembly([])
    assert m.part_bboxes == []
    assert m.order == []


# --- the swept volume -----------------------------------------------------

PIN_CLEARANCE = 0.05


def _bored_block():
    """A block with a round hole straight through it, and the pin standing in it.

    The pin runs out of both ends, so the block slides off it along the bore and
    is caught by it every other way. The gap is a running fit's, far below the
    smallest step a stepped search would take.
    """
    block = Box(30, 30, 20) - Cylinder(5 + PIN_CLEARANCE, 22)
    return [block, Cylinder(5, 40)]


def _pin_in_a_blind_hole():
    """A pin in a hole that stops short of the block's far face.

    The hole's floor is the one face of the block that stops it rising off the
    pin, and the pin sits wholly beyond where that face starts -- so only the
    face's prism reaches it, never the face read where it lies.
    """
    block = Box(30, 30, 20) - Pos(0, 0, 3) * Cylinder(5 + PIN_CLEARANCE, 15)
    pin = Pos(0, 0, 5.55) * Cylinder(5, 20)
    return [block, pin]


def _rule(parts, index, heading):
    """How the search rules one part sliding along ``heading``: free, blocked or undecided."""
    import cadless.assembly_check as module

    spheres = [module._sphere(part) for part in parts]
    others = [other for other in range(len(parts)) if other != index]
    budget = module._Budget(10**6, 600.0)
    return module._rule_heading(
        parts,
        spheres,
        index,
        others,
        Vector(*heading).normalized(),
        module._look_ahead(parts),
        budget,
    )


def test_a_clash_shorter_than_the_smallest_step_is_not_jumped():
    # Measured on this pair: sliding the first cube along (1,1,0) clips the second
    # for t in [0.071, 0.183] mm of travel, up to 0.016 mm^3 deep. They start
    # 0.05 mm apart, so a search that steps by the larger of the gap and a floor
    # of 2% of a cube (0.2 mm) lands first at 0.2, past the clash.
    first = Box(10, 10, 10, align=(Align.MIN, Align.MIN, Align.MIN))
    second = Pos(10.05, -9.87, 0) * Box(10, 10, 10, align=(Align.MIN, Align.MIN, Align.MIN))
    assert _rule([first, second], 0, (1, 1, 0)) == BLOCKED, "stepped over a real collision"


def test_a_pin_in_a_close_bore_is_ruled_within_a_small_budget():
    # A stepped search pays one probe per floor step to slide the block along the
    # pin, a few hundred here; settling the pin inside the bore costs a handful.
    m = measure_assembly(_bored_block(), probe_budget=60)
    assert m.unchecked == []
    assert m.order is not None


def _vase_and_a_block_grazing_its_bulge(depth=0.03):
    """A revolved-spline vase, and a block whose path cuts ``depth`` into its bulge.

    A surface of revolution is the kind a quick bounding box only samples, and
    the bulge pokes out past such a box -- so a block passing that close is the
    case where a prefilter on loose boxes skips a boolean that clashes.
    """
    from build123d import (
        Axis,
        BuildLine,
        BuildPart,
        BuildSketch,
        Line,
        Plane,
        Spline,
        make_face,
        revolve,
    )
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepTools import BRepTools

    with BuildPart() as made:
        with BuildSketch(Plane.XZ):
            with BuildLine():
                Spline((0, 0), (24, 9), (15, 30), (27, 54), (0, 66))
                Line((0, 66), (0, 0))
            make_face()
        revolve(axis=Axis.Z)
    vase = made.part
    widest = (float("-inf"), 0.0)
    for face in vase.faces():
        umin, umax, vmin, vmax = BRepTools.UVBounds_s(face.wrapped)
        surface = BRepAdaptor_Surface(face.wrapped)
        for step in range(4001):
            point = surface.Value(umin, vmin + (vmax - vmin) * step / 4000)
            widest = max(widest, (point.X(), point.Z()))
    reach, height = widest
    return [Pos(reach - depth + 2.5, -60, height) * Box(5, 2, 4), vase]


def test_a_block_grazing_a_curved_bulge_is_stopped_by_it():
    from build123d import Location

    from cadless.assembly_check import OVERLAP_EPSILON_MM3, _shared_volume

    block, vase = _vase_and_a_block_grazing_its_bulge()
    crossing = block.moved(Location(Vector(0, 60, 0)))
    assert _shared_volume(crossing, vase) > OVERLAP_EPSILON_MM3, "the fixture lost its clash"
    assert _rule([block, vase], 0, (0, 1, 0)) == BLOCKED


def test_a_lid_resting_on_its_box_lifts_straight_off():
    box = Box(20, 20, 10)
    lid = Pos(0, 0, 6) * Box(20, 20, 2)
    assert _rule([box, lid], 1, (0, 0, 1)) == FREE


def test_a_block_slides_along_the_face_it_rests_on():
    # Contact along a whole face, the case where a boolean on coplanar faces can
    # leave a sliver of volume that is not there and refuse a free part.
    base = Box(20, 20, 10)
    block = Pos(0, 0, 10) * Box(20, 20, 10)
    assert _rule([base, block], 1, (1, 0, 0)) == FREE


def test_a_block_cannot_rise_off_a_pin_that_bottoms_in_its_hole():
    assert _rule(_pin_in_a_blind_hole(), 0, (0, 0, 1)) == BLOCKED


def test_a_block_drops_off_a_pin_that_leaves_its_hole():
    assert _rule(_pin_in_a_blind_hole(), 0, (0, 0, -1)) == FREE


def _channel_and_bar():
    """An open channel and a bar lying loose in its slot, longer than the channel.

    The channel slides off along the slot, and the prism of its end face reaches
    over the bar's protruding end on the way -- so ruling it free takes a boolean
    that comes back empty, rather than a box test that never needed one.
    """
    channel = Box(40, 20, 10) - Pos(0, 0, 2) * Box(42, 10.4, 10)
    return [channel, Box(60, 10, 4)]


@pytest.mark.parametrize("heading, verdict", [((1, 0, 0), FREE), ((0, 0, 1), BLOCKED)])
def test_a_prism_boolean_the_kernel_refuses_has_the_pair_ruled_another_way(
    monkeypatch, heading, verdict
):
    # A refused boolean is neither a clash nor a clear path, so the pair is ruled
    # another way: along the slot, by the bar swept back against the channel; up,
    # by walking the channel until its floor meets the bar.
    import cadless.assembly_check as module

    monkeypatch.setattr(module, "_prism_overlap", lambda prism, other: None)
    assert _rule(_channel_and_bar(), 0, heading) == verdict


def test_a_sweep_the_kernel_will_not_build_leaves_the_pair_to_the_walk(monkeypatch):
    # A refused sweep is no clash, so blocking on it refuses a part that comes
    # out freely; the pair is walked instead, which measures it another way.
    import cadless.assembly_check as module

    monkeypatch.setattr(module, "_sweep_pieces", lambda part, direction, length: None)
    assert _rule(_channel_and_bar(), 0, (1, 0, 0)) == FREE


def test_a_pair_that_can_be_neither_swept_nor_measured_is_left_undecided(monkeypatch):
    import cadless.assembly_check as module

    monkeypatch.setattr(module, "_sweep_pieces", lambda part, direction, length: None)
    monkeypatch.setattr(module, "_distance", lambda first, second: None)
    assert _rule(_channel_and_bar(), 0, (1, 0, 0)) == UNDECIDED


def _swept_volume(part, heading, length):
    """The volume of the part fused with the pieces of its sweep."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    import cadless.assembly_check as module

    pieces = module._sweep_pieces(part, Vector(*heading).normalized(), length)
    assert pieces is not None
    shape = part.wrapped
    for prism, _ in pieces:
        shape = BRepAlgoAPI_Fuse(shape, prism).Shape()
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    return props.Mass()


def _shadow_area(rims, heading):
    """The area of the convex hull of ``rims`` seen along ``heading``.

    For a convex solid the volume it sweeps over a straight travel ``L`` is its
    own volume plus ``L`` times that area -- an answer that owes nothing to the
    kernel, which is what makes it an oracle for the sweep.
    """
    import numpy as np

    d = np.asarray(heading, dtype=float)
    d /= np.linalg.norm(d)
    first = np.cross(d, [0.0, 0.0, 1.0] if abs(d[2]) < 0.9 else [1.0, 0.0, 0.0])
    first /= np.linalg.norm(first)
    second = np.cross(d, first)
    flat = sorted({(float(p @ first), float(p @ second)) for p in np.asarray(rims)})

    def half(points):
        hull = []
        for p in points:
            while (
                len(hull) > 1
                and (
                    (hull[-1][0] - hull[-2][0]) * (p[1] - hull[-2][1])
                    - (hull[-1][1] - hull[-2][1]) * (p[0] - hull[-2][0])
                )
                <= 0
            ):
                hull.pop()
            hull.append(p)
        return hull

    ring = half(flat)[:-1] + half(flat[::-1])[:-1]
    xs, ys = np.array([p[0] for p in ring]), np.array([p[1] for p in ring])
    return 0.5 * abs(float(np.dot(xs, np.roll(ys, -1)) - np.dot(ys, np.roll(xs, -1))))


def _rim(radius, z, samples=4000):
    import numpy as np

    angles = np.linspace(0.0, 2.0 * np.pi, samples, endpoint=False)
    return np.stack([radius * np.cos(angles), radius * np.sin(angles), np.full(samples, z)], 1)


@pytest.mark.parametrize("heading", [(1, 0, 0), (0, 0, 1), (1, 0, 1), (0.3, -0.5, 0.8)])
def test_a_cylinder_sweeps_exactly_the_volume_it_passes_over(heading):
    import math

    import numpy as np

    length = 20.0
    expected = math.pi * 25 * 10 + length * _shadow_area(
        np.concatenate([_rim(5, -5), _rim(5, 5)]), heading
    )
    assert _swept_volume(Cylinder(5, 10), heading, length) == pytest.approx(expected, rel=1e-4)


@pytest.mark.parametrize("heading", [(1, 0, 0), (0, 0, 1), (0, 0, -1), (1, 0, 1), (0.3, -0.5, 0.8)])
def test_a_cone_sweeps_exactly_the_volume_it_passes_over(heading):
    # The oblique headings are the ones that cut a cone off its axis-square
    # silhouette: a cut placed as for a cylinder folds the prism and still
    # builds a solid, only with the wrong volume.
    import math

    import numpy as np

    length = 20.0
    frustum = math.pi * 10 / 3 * (6 * 6 + 6 * 3 + 3 * 3)
    expected = frustum + length * _shadow_area(np.concatenate([_rim(6, -5), _rim(3, 5)]), heading)
    swept = _swept_volume(Cone(6, 3, 10), heading, length)
    assert swept == pytest.approx(expected, rel=1e-4)


@pytest.mark.parametrize("heading", [(1, 0, 0), (1, 1, 0)])
def test_a_sphere_sweeps_exactly_the_volume_it_passes_over(heading):
    # Headings the oracle's own fuse can take: at some oblique ones the kernel
    # fuses the two halves' prisms to nothing, though each is right on its own.
    import math

    length = 20.0
    expected = 4 / 3 * math.pi * 5**3 + length * math.pi * 5**2
    assert _swept_volume(Sphere(5), heading, length) == pytest.approx(expected, rel=1e-4)


def _halves_built_inside_out(heading):
    """How many halves of a ball the kernel prisms inside out along ``heading``."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepGProp import BRepGProp
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.gp import gp_Vec
    from OCP.GProp import GProp_GProps

    import cadless.assembly_check as module

    path = Vector(*heading).normalized()
    face = Sphere(5).faces()[0].wrapped
    halves = module._cut_by_planes(face, module._silhouette_planes(BRepAdaptor_Surface(face), path))
    inside_out = 0
    for half in halves:
        props = GProp_GProps()
        prism = BRepPrimAPI_MakePrism(half, gp_Vec(path.X * 20, path.Y * 20, path.Z * 20))
        BRepGProp.VolumeProperties_s(prism.Shape(), props)
        inside_out += props.Mass() < 0
    return inside_out


BALL_HEADINGS = [(-0.639, 0.739, -0.213), (0.576, 0.694, 0.432)]


@pytest.mark.parametrize("heading", BALL_HEADINGS)
def test_a_ball_passes_a_block_beside_its_path(heading):
    # At these headings the kernel builds one half's prism inside out. Read as it
    # comes, it holds everything outside the ball's path, so the block beside the
    # path reads as a clash.
    assert _halves_built_inside_out(heading) > 0, "the kernel no longer does this here"
    path = Vector(*heading).normalized()
    beside = path.cross(Vector(0, 0, 1)).normalized()
    block = Pos(*(path * 10 + beside * 8)) * Box(2, 2, 2)
    assert _rule([Sphere(5), block], 0, heading) == FREE


@pytest.mark.parametrize("heading", BALL_HEADINGS)
def test_a_ball_is_stopped_by_a_block_in_its_path(heading):
    path = Vector(*heading).normalized()
    block = Pos(*(path * 10)) * Box(2, 2, 2)
    assert _rule([Sphere(5), block], 0, heading) == BLOCKED


def test_a_torus_sweeps_exactly_the_volume_it_passes_over_along_its_axis():
    # Every line along the axis meets the ring in one stretch at most, so the
    # sweep is the ring plus the travel times the annulus it shades.
    import math

    major, minor, length = 10.0, 3.0, 20.0
    ring = 2 * math.pi**2 * major * minor**2
    expected = ring + length * math.pi * ((major + minor) ** 2 - (major - minor) ** 2)
    swept = _swept_volume(Torus(major, minor), (0, 0, 1), length)
    assert swept == pytest.approx(expected, rel=1e-4)


@pytest.mark.parametrize("heading", [(1, 0, 0), (1, 1, 0)])
def test_a_torus_sweeps_exactly_the_volume_it_passes_over_across_its_axis(heading):
    # A line across the axis can meet the ring twice, either side of the hole.
    # Travelling further than the hole is wide fills the hole in, so the sweep is
    # the ring with its hole filled -- revolve R + sqrt(r^2 - z^2) about the axis --
    # plus the travel times the shadow, 4Rr + pi r^2.
    import math

    major, minor, length = 10.0, 3.0, 20.0
    assert length >= 2 * major  # wide enough to close the hole's widest chord
    filled = math.pi * (2 * minor * major**2 + math.pi * major * minor**2 + 4 / 3 * minor**3)
    expected = filled + length * (4 * major * minor + math.pi * minor**2)
    swept = _swept_volume(Torus(major, minor), heading, length)
    assert swept == pytest.approx(expected, rel=1e-4)


def _block_with_curved_skin():
    """The bored block with B-spline outer faces round a true cylindrical bore.

    Neither it nor a B-spline pin can be swept, so only the bore can settle the
    pair without walking it.
    """
    return _as_nurbs(Box(30, 30, 20)) - Cylinder(5 + PIN_CLEARANCE, 22)


def test_a_pin_inside_a_bore_is_settled_by_the_bore_alone():
    pin = _as_nurbs(Cylinder(5, 40))
    m = measure_assembly([pin, _block_with_curved_skin()], probe_budget=60)
    assert m.unchecked == []
    assert m.order is not None


def test_a_pin_with_a_head_wider_than_its_bore_is_not_settled_by_the_bore():
    # The head reaches past the bore's radius, so the pin is not inside the bore
    # and the bore must not answer for it: pushed further in, the head meets the
    # block.
    pin = _as_nurbs(Cylinder(5, 40) + Pos(0, 0, 20) * Cylinder(8, 4))
    assert _rule([pin, _block_with_curved_skin()], 0, (0, 0, -1)) == BLOCKED


def test_a_part_of_two_solids_is_not_settled_by_a_bore_one_of_them_sits_in():
    # The second solid sits outside the bore's cylinder, so the part is not inside
    # the bore though one of its solids is -- and pushed down, it lands on the block.
    from build123d import Compound

    pin = Compound([Cylinder(5, 40), Pos(12, 0, 15) * Box(2, 2, 2)])
    assert _rule([pin, _block_with_curved_skin()], 0, (0, 0, -1)) == BLOCKED


def _pin_bulging_past_its_bore():
    """A lofted pin whose middle bulges 0.01 mm past its bore, above a bored block.

    The pin's faces are B-splines, and at this orientation a distance query
    between it and the bore's cylinder misses where its face crosses the cylinder
    away from every edge -- reporting a gap of about 0.015 mm that is not there.
    Returns the pin, the block and the heading that pushes the pin into the bore.
    """
    from build123d import BuildPart, BuildSketch, Circle, Locations, Plane, loft

    bore = 5 + PIN_CLEARANCE
    turn = Rot(16.178, 258.639, 119.143)
    with BuildPart() as made:
        for level in range(5):
            plane = Plane.XY.offset(-20 + 10 * level).rotated((0, 0, 221.232))
            with BuildSketch(plane):
                with Locations((0, bore + 0.01 - 5 if level == 2 else 0)):
                    Circle(5)
        loft()
    pin = turn * (Pos(0, 0, 31) * made.part)
    block = turn * (Box(30, 30, 20) - Cylinder(bore, 22))
    down = Vector((turn * Pos(0, 0, -1)).position).normalized()
    return pin, block, down


def test_a_pin_bulging_past_its_bore_is_not_settled_by_the_bore():
    from build123d import Location

    from cadless.assembly_check import OVERLAP_EPSILON_MM3, _shared_volume

    pin, block, down = _pin_bulging_past_its_bore()
    deepest = max(_shared_volume(pin.moved(Location(down * t)), block) for t in (21, 25, 31))
    assert deepest > OVERLAP_EPSILON_MM3, "the fixture lost its clash"
    assert _rule([pin, block], 0, (down.X, down.Y, down.Z)) == BLOCKED


def test_a_bore_whose_floor_the_distance_misses_does_not_settle_its_pin(monkeypatch):
    # Make the distance to the axis report the blind hole as an open 5.05 mm bore,
    # as a search that misses the hole's floor would. The floor still sits inside
    # the test cylinder, so the volume proof refuses the pair, and the pin pushed
    # down into the floor is still stopped by it.
    from build123d import Edge

    import cadless.assembly_check as module

    block, pin = _pin_in_a_blind_hole()
    real = module._distance

    def missing_the_floor(first, second):
        if first is block and isinstance(second, Edge):
            return 5 + PIN_CLEARANCE
        return real(first, second)

    monkeypatch.setattr(module, "_distance", missing_the_floor)
    assert _rule([block, pin], 1, (0, 0, -1)) == BLOCKED


def test_a_part_whose_box_cannot_be_taken_is_not_read_as_clear(monkeypatch):
    # The channel rising meets the bar. With no box for the bar, the channel's own
    # sweep cannot be checked against it, and that must not read as nothing met.
    import cadless.assembly_check as module

    channel, bar = _channel_and_bar()
    real = module._box_of
    monkeypatch.setattr(
        module, "_box_of", lambda shape: None if shape is bar.wrapped else real(shape)
    )
    assert _rule([channel, bar], 0, (0, 0, 1)) == BLOCKED


def test_a_walk_never_steps_over_a_clash_shorter_than_the_smallest_step():
    # The same clip as the plain cubes', on cubes neither of which can be swept,
    # so the walk alone rules it: it may stop short at the contact, never pass it.
    first = Box(10, 10, 10, align=(Align.MIN, Align.MIN, Align.MIN))
    second = Pos(10.05, -9.87, 0) * Box(10, 10, 10, align=(Align.MIN, Align.MIN, Align.MIN))
    assert _rule([_as_nurbs(first), _as_nurbs(second)], 0, (1, 1, 0)) != FREE


def test_parts_the_walk_cannot_see_past_are_named_rather_than_called_trapped():
    # Two blocks touching face to face, with no exact sweep on either: every way
    # one moves either meets the other or stops at a contact nothing measured
    # past. That is not a trapped part, and the line says which parts and why.
    pair = [_as_nurbs(Box(20, 20, 10)), _as_nurbs(Pos(20, 0, 0) * Box(20, 20, 10))]
    m = measure_assembly(pair)
    assert m.order is None
    assert m.trapped == []
    assert any("part 1 and part 2 touch" in reason for reason in m.unchecked)


def test_a_heading_away_from_a_part_that_is_already_an_axis_is_tried_once():
    # The hinge's case: the leaf sits straight along -X from the pin, so leading
    # away from it is +X, which the axes already hold.
    import cadless.assembly_check as module

    spheres = [((0.0, 0.0, 0.0), 1.0), ((-5.0, 0.0, 0.0), 1.0)]
    headings = module._candidate_directions(spheres, 0, [1])
    assert sum(1 for heading in headings if (heading - Vector(1, 0, 0)).length < 1e-9) == 1


def test_the_order_search_does_not_ask_again_what_the_pairwise_phase_measured(monkeypatch):
    # Two blocks that sweep exactly: their shared volume where they stand is asked
    # once, by the pairwise phase, and not again for every heading tried.
    import cadless.assembly_check as module

    asked = []
    real = module._shared_volume

    def counted(first, second):
        asked.append(1)
        return real(first, second)

    monkeypatch.setattr(module, "_shared_volume", counted)
    assert measure_assembly(_mated_pair()).order is not None
    assert len(asked) == 1


def _curved_bar_in_a_channel():
    """The channel and bar with the bar's faces all B-splines: it cannot be swept.

    The channel can, and has no round face to settle the pair as a bore, so the
    bar is ruled on the channel swept back against it.
    """
    channel, bar = _channel_and_bar()
    return [_as_nurbs(bar), channel]


def test_a_part_that_cannot_be_swept_lifts_out_on_the_other_swept_back():
    assert _rule(_curved_bar_in_a_channel(), 0, (0, 0, 1)) == FREE


def test_a_part_that_cannot_be_swept_meets_what_the_other_swept_back_reaches():
    # Down, the bar lands on the slot's floor a millimetre below it.
    assert _rule(_curved_bar_in_a_channel(), 0, (0, 0, -1)) == BLOCKED


def test_a_part_that_cannot_be_swept_is_ruled_within_a_small_budget():
    # Walking the bar along its slot, 0.2 mm from each wall, would take hundreds of
    # probes; sweeping the channel back takes a handful.
    m = measure_assembly(_curved_bar_in_a_channel(), probe_budget=60)
    assert m.unchecked == []
    assert m.order is not None


def test_what_the_measurement_could_not_establish_names_the_parts():
    """The same sentence a reader is shown, so the same names."""
    m = measure_assembly([_Awkward(), _Awkward(centre=(30.0, 0.0, 0.0))], names=["base", "lid"])
    assert len(m.unchecked) >= 2
    assert all("base and lid" in reason for reason in m.unchecked)


def test_a_clash_probe_that_cannot_run_names_the_parts(monkeypatch):
    import cadless.assembly_check as module

    monkeypatch.setattr(module, "_shared_volume", lambda first, second: None)
    m = measure_assembly([Box(20, 20, 10), Pos(20, 0, 0) * Box(20, 20, 10)], names=["base", "lid"])
    assert any("could not measure base and lid" in reason for reason in m.unchecked)


def test_parts_the_walk_cannot_see_past_are_named_by_their_build():
    pair = [_as_nurbs(Box(20, 20, 10)), _as_nurbs(Pos(20, 0, 0) * Box(20, 20, 10))]
    m = measure_assembly(pair, names=["base", "lid"])
    assert any("base and lid touch" in reason for reason in m.unchecked)
