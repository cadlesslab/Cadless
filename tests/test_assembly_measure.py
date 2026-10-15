"""Assembly measurement tests. Marked build123d: these execute the OCCT kernel.

The policy that reads these numbers is tested in ``tests/test_assembly_check.py``
against synthetic values; here the numbers themselves come from real solids.
"""

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
    fillet,
)

from cadless.assembly_check import measure_assembly

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
    # millimetres of travel, so only advancing by the measured separation catches
    # it.
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
    how thick either part is, so no fraction of a part bounds it. Advancing by
    the measured separation is what closes this, and it is the only case in the
    suite where the two strategies disagree.
    """
    from build123d import Vector

    import cadless.assembly_check as module

    first = Pos(5, 5, 5) * Box(10, 10, 10)
    second = Pos(25, 5.5, 5) * Box(10, 10, 10)
    parts = [first, second]
    spheres = [module._sphere(part) for part in parts]
    heading = Vector(1, 1, 0).normalized()
    budget = module._Budget(10**6, 600.0)

    blocked = module._blocked_along(
        parts, spheres, 0, [1], heading, module._min_step(parts), budget
    )
    assert blocked, "the search walked past a real collision"


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


def test_a_part_with_no_measurable_size_stops_the_order_search():
    m = measure_assembly([_Awkward(size=(0.0, 10.0, 10.0)), _Awkward(centre=(30.0, 0.0, 0.0))])
    assert m.order is None
    assert any("no measurable size" in reason for reason in m.unchecked)


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
    return Solid(TopoDS.Solid_s(TopExp_Explorer(converted, TopAbs_SOLID).Current()))


def test_a_distance_probe_that_cannot_run_blocks_rather_than_clears(monkeypatch):
    # Where the search walks, it advances by the measured separation, so the
    # distance query is what it rests on. One the kernel could not answer must not
    # read as "nothing in the way": that frees a part on no evidence and returns
    # an order that may not work.
    import cadless.assembly_check as module

    monkeypatch.setattr(module, "_distance", lambda first, second: None)
    m = measure_assembly([_as_nurbs(part) for part in _mated_pair()])
    assert m.order is None
    assert m.trapped == [0, 1]


def test_a_clash_probe_that_cannot_run_blocks_rather_than_clears(monkeypatch):
    # The other half: once two parts are in contact the separation no longer
    # decides it and the shared volume does, so a refused volume query blocks too.
    import cadless.assembly_check as module

    monkeypatch.setattr(module, "_shared_volume", lambda first, second: None)
    m = measure_assembly([Box(20, 20, 10), Pos(20, 0, 0) * Box(20, 20, 10)])
    assert m.order is None
    assert m.trapped == [0, 1]


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
    import cadless.assembly_check as module

    spheres = [module._sphere(part) for part in parts]
    others = [other for other in range(len(parts)) if other != index]
    budget = module._Budget(10**6, 600.0)
    return module._blocked_along(
        parts,
        spheres,
        index,
        others,
        Vector(*heading).normalized(),
        module._min_step(parts),
        budget,
    )


def test_a_clash_shorter_than_the_smallest_step_is_not_jumped():
    # Measured on this pair: sliding the first cube along (1,1,0) clips the second
    # for t in [0.071, 0.183] mm of travel, up to 0.016 mm^3 deep. They start
    # 0.05 mm apart, so a search that steps by the larger of the gap and a floor
    # of 2% of a cube (0.2 mm) lands first at 0.2, past the clash.
    first = Box(10, 10, 10, align=(Align.MIN, Align.MIN, Align.MIN))
    second = Pos(10.05, -9.87, 0) * Box(10, 10, 10, align=(Align.MIN, Align.MIN, Align.MIN))
    assert _rule([first, second], 0, (1, 1, 0)), "the search stepped over a real collision"


def test_a_pin_in_a_close_bore_is_ruled_within_a_small_budget():
    # A stepped search pays one probe per floor step to slide the block along the
    # pin, a few hundred here; ruling on the volume swept costs a handful.
    m = measure_assembly(_bored_block(), probe_budget=60)
    assert m.unchecked == []
    assert m.order is not None


def test_a_lid_resting_on_its_box_lifts_straight_off():
    box = Box(20, 20, 10)
    lid = Pos(0, 0, 6) * Box(20, 20, 2)
    assert not _rule([box, lid], 1, (0, 0, 1))


def test_a_block_slides_along_the_face_it_rests_on():
    # Contact along a whole face, the case where a boolean on coplanar faces can
    # leave a sliver of volume that is not there and refuse a free part.
    base = Box(20, 20, 10)
    block = Pos(0, 0, 10) * Box(20, 20, 10)
    assert not _rule([base, block], 1, (1, 0, 0))


def test_a_block_cannot_rise_off_a_pin_that_bottoms_in_its_hole():
    assert _rule(_pin_in_a_blind_hole(), 0, (0, 0, 1))


def test_a_block_drops_off_a_pin_that_leaves_its_hole():
    assert not _rule(_pin_in_a_blind_hole(), 0, (0, 0, -1))


def _channel_and_bar():
    """An open channel and a bar lying loose in its slot, longer than the channel.

    The channel slides off along the slot, and the prism of its end face reaches
    over the bar's protruding end on the way -- so ruling it free takes a boolean
    that comes back empty, rather than a box test that never needed one.
    """
    channel = Box(40, 20, 10) - Pos(0, 0, 2) * Box(42, 10.4, 10)
    return [channel, Box(60, 10, 4)]


def test_a_prism_boolean_that_cannot_run_blocks_rather_than_clears(monkeypatch):
    import cadless.assembly_check as module

    assert not _rule(_channel_and_bar(), 0, (1, 0, 0))
    monkeypatch.setattr(module, "_prism_overlap", lambda prism, other: None)
    assert _rule(_channel_and_bar(), 0, (1, 0, 0))


def test_a_sweep_that_cannot_be_built_blocks_rather_than_clears(monkeypatch):
    import cadless.assembly_check as module

    assert not _rule(_channel_and_bar(), 0, (1, 0, 0))
    monkeypatch.setattr(module, "_sweep_pieces", lambda part, direction, length: None)
    assert _rule(_channel_and_bar(), 0, (1, 0, 0))


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


@pytest.mark.parametrize("heading", [(-0.639, 0.739, -0.213), (0.576, 0.694, 0.432)])
def test_a_ball_passes_a_block_beside_its_path(heading):
    # At these headings the kernel builds one half's prism inside out. Read as it
    # comes, it holds everything outside the ball's path, so the block beside the
    # path reads as a clash.
    path = Vector(*heading).normalized()
    beside = path.cross(Vector(0, 0, 1)).normalized()
    block = Pos(*(path * 10 + beside * 8)) * Box(2, 2, 2)
    assert not _rule([Sphere(5), block], 0, heading)


@pytest.mark.parametrize("heading", [(-0.639, 0.739, -0.213), (0.576, 0.694, 0.432)])
def test_a_ball_is_stopped_by_a_block_in_its_path(heading):
    path = Vector(*heading).normalized()
    block = Pos(*(path * 10)) * Box(2, 2, 2)
    assert _rule([Sphere(5), block], 0, heading)


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
    assert length > 2 * (major - minor)
    filled = math.pi * (2 * minor * major**2 + math.pi * major * minor**2 + 4 / 3 * minor**3)
    expected = filled + length * (4 * major * minor + math.pi * minor**2)
    swept = _swept_volume(Torus(major, minor), heading, length)
    assert swept == pytest.approx(expected, rel=1e-4)


def test_a_pin_with_rounded_ends_in_a_close_bore_is_ruled_within_a_small_budget():
    # The rounding is a torus each end, which the search has to sweep exactly to
    # take the pin out along its bore rather than walk it.
    pin = fillet(Cylinder(5, 40).edges().filter_by(GeomType.CIRCLE), radius=1)
    block = _bored_block()[0]
    m = measure_assembly([pin, block], probe_budget=60)
    assert m.unchecked == []
    assert m.order is not None


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
    assert _rule([pin, _block_with_curved_skin()], 0, (0, 0, -1))


def test_a_part_of_two_solids_is_not_settled_by_a_bore_one_of_them_sits_in():
    # The second solid sits clear of the bore's cylinder, so nothing connects it
    # to the one inside -- and pushed down, it lands on the block.
    from build123d import Compound

    pin = Compound([Cylinder(5, 40), Pos(12, 0, 15) * Box(2, 2, 2)])
    assert _rule([pin, _block_with_curved_skin()], 0, (0, 0, -1))


def test_a_walk_never_steps_over_a_clash_shorter_than_the_smallest_step():
    # The same clip as the plain cubes', on cubes neither of which can be swept,
    # so the walk alone rules it: it may stop short at the contact, never pass it.
    first = Box(10, 10, 10, align=(Align.MIN, Align.MIN, Align.MIN))
    second = Pos(10.05, -9.87, 0) * Box(10, 10, 10, align=(Align.MIN, Align.MIN, Align.MIN))
    assert _rule([_as_nurbs(first), _as_nurbs(second)], 0, (1, 1, 0))


def test_parts_the_walk_cannot_see_past_are_named_rather_than_called_trapped():
    # Two blocks touching face to face, with no exact sweep on either: every way
    # one moves either meets the other or stops at a contact nothing measured
    # past. That is not a trapped part, and the line says which parts and why.
    pair = [_as_nurbs(Box(20, 20, 10)), _as_nurbs(Pos(20, 0, 0) * Box(20, 20, 10))]
    m = measure_assembly(pair)
    assert m.order is None
    assert m.trapped == []
    assert any("part 1 and part 2 touch" in reason for reason in m.unchecked)


def test_a_part_with_no_exact_sweep_is_ruled_on_the_other_part_swept_back():
    # The pin's faces are all B-splines, so it cannot be swept; the block can, and
    # sliding the pin out is sliding the block back off it.
    pin = _as_nurbs(Cylinder(5, 40))
    block = _bored_block()[0]
    m = measure_assembly([pin, block], probe_budget=60)
    assert m.unchecked == []
    assert m.order is not None
