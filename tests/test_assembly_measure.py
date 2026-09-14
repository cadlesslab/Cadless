"""Assembly measurement tests. Marked build123d: these execute the OCCT kernel.

The policy that reads these numbers is tested in ``tests/test_assembly_check.py``
against synthetic values; here the numbers themselves come from real solids.
"""

import pytest
from build123d import Box, Pos, Rot

from cadless.assembly_check import measure_assembly

pytestmark = pytest.mark.build123d

CLEARANCE = 0.2


def _mated_pair():
    """Two blocks sharing a face, separated by the joint clearance."""
    return [Box(20, 20, 10), Pos(20 + CLEARANCE, 0, 0) * Box(20, 20, 10)]


def _caged():
    """A small block walled in on all six sides -- assemblable in no order."""
    inner = Box(6, 6, 6)
    walls = [
        Pos(0, 8, 0) * Box(20, 10, 20),
        Pos(0, -8, 0) * Box(20, 10, 20),
        Pos(0, 0, 8) * Box(20, 20, 10),
        Pos(0, 0, -8) * Box(20, 20, 10),
        Pos(8, 0, 0) * Box(10, 20, 20),
        Pos(-8, 0, 0) * Box(10, 20, 20),
    ]
    return [inner, *walls]


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


def test_a_caged_part_has_no_order_and_is_named():
    m = measure_assembly(_caged())
    assert m.order is None
    assert 0 in m.trapped


def test_a_blocker_thinner_than_a_coarse_step_is_not_jumped():
    # The failure this guards: with a step derived from the span rather than from
    # the geometry, the first move clears a thin wall entirely and the part reads
    # as free. The wall here is deliberately thin next to the travel distance.
    inner = Box(6, 6, 6)
    walls = [
        Pos(0, 6, 0) * Box(40, 2, 40),
        Pos(0, -6, 0) * Box(40, 2, 40),
        Pos(0, 0, 6) * Box(40, 40, 2),
        Pos(0, 0, -6) * Box(40, 40, 2),
        Pos(6, 0, 0) * Box(2, 40, 40),
        Pos(-6, 0, 0) * Box(2, 40, 40),
    ]
    m = measure_assembly([inner, *walls])
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


# --- failing closed -------------------------------------------------------


def test_a_collision_probe_that_cannot_run_blocks_rather_than_clears(monkeypatch):
    # A probe the kernel could not answer must not read as "nothing in the way":
    # that frees a part on no evidence and hands back an order that may not work.
    import cadless.assembly_check as module

    monkeypatch.setattr(module, "_shared_volume", lambda first, second: None)
    m = measure_assembly(_mated_pair())
    assert m.order is None
    assert m.trapped == [0, 1]


def test_a_spent_probe_budget_is_reported_rather_than_guessed():
    m = measure_assembly(_caged(), probe_budget=1)
    assert m.order is None
    assert m.unchecked
    assert any("order" in reason for reason in m.unchecked)


def test_a_spent_budget_does_not_masquerade_as_a_trapped_part():
    # "We ran out of probes" and "this part is walled in" are different answers
    # and the policy layer reports them differently, so they must not be conflated.
    m = measure_assembly(_caged(), probe_budget=1)
    assert m.trapped == []


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
