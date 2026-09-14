"""Assembly post-condition policy tests.

These exercise the pure evaluation function with synthetic measurements (part
boxes, pairwise overlap volumes and gaps, an assembly order), so they need no
live OCCT. The geometry that produces those numbers is tested in
``tests/test_worker.py``.
"""

import pytest

from cadless.assembly_check import (
    AssemblyMeasurements,
    AssemblyReport,
    evaluate_assembly,
    mating_tolerance,
)
from cadless.printer_profile import AssemblySpec, BuildVolume

SPEC = AssemblySpec(volume=BuildVolume(210.0, 200.0, 195.0), clearance_mm=0.2)


def _m(**kw) -> AssemblyMeasurements:
    """A two-part assembly that passes every check, overridable per test."""
    base = dict(
        part_bboxes=[[100.0, 100.0, 50.0], [100.0, 100.0, 50.0]],
        overlaps=[],
        gaps=[[0, 1, 0.2]],
        order=[0, 1],
        trapped=[],
        unchecked=[],
    )
    base.update(kw)
    return AssemblyMeasurements(**base)


# --- nothing to check -----------------------------------------------------


def test_no_measurements_passes_with_no_signal():
    report = evaluate_assembly(None, SPEC)
    assert report.ok
    assert report.repair_signal() is None


def test_single_part_is_not_an_assembly_and_is_never_checked():
    # A one-part result has no relation to check. Its box is deliberately far too
    # big for the bed: even so, nothing here may fire -- judging a single part
    # against the build volume is print_fit's job, not this module's.
    report = evaluate_assembly(_m(part_bboxes=[[9999.0, 9999.0, 9999.0]], gaps=[], order=[0]), SPEC)
    assert report.ok
    assert report.failures == []


def test_a_good_split_passes_and_carries_its_order():
    report = evaluate_assembly(_m(), SPEC)
    assert report.ok
    assert report.failures == []
    assert report.order == [0, 1]
    assert report.repair_signal() is None


# --- every part fits the build volume -------------------------------------


def test_a_part_too_big_for_the_bed_is_refused_and_named():
    report = evaluate_assembly(_m(part_bboxes=[[100.0, 100.0, 50.0], [250.0, 40.0, 30.0]]), SPEC)
    assert not report.ok
    assert len(report.failures) == 1
    # The repair signal has to say which part and give the two sizes, or the model
    # cannot act on it.
    assert "part 2" in report.failures[0]
    assert "250" in report.failures[0]
    assert "210" in report.failures[0]


def test_a_part_that_fits_only_turned_on_the_bed_is_accepted():
    # 200 x 205 does not fit 210 x 200 as it stands but does turned a quarter, and
    # the slicer may place it either way. Refusing it would be the stricter answer.
    report = evaluate_assembly(_m(part_bboxes=[[100.0, 100.0, 50.0], [200.0, 205.0, 30.0]]), SPEC)
    assert report.ok


def test_a_part_taller_than_the_bed_is_refused():
    report = evaluate_assembly(_m(part_bboxes=[[100.0, 100.0, 50.0], [100.0, 100.0, 400.0]]), SPEC)
    assert not report.ok
    assert "part 2" in report.failures[0]


@pytest.mark.parametrize(
    "bad_box",
    [
        [100.0, 100.0],  # short
        ["wide", 100.0, 50.0],  # not a number
        [float("nan"), 100.0, 50.0],  # not finite
        [float("inf"), 100.0, 50.0],
    ],
)
def test_an_unreadable_part_box_refuses_rather_than_passing(bad_box):
    # print_fit answers "" for a box it cannot read, and every caller there reads
    # that as silence. Here silence would present an unchecked assembly as a
    # checked one, so it has to fail closed instead.
    report = evaluate_assembly(_m(part_bboxes=[[100.0, 100.0, 50.0], bad_box]), SPEC)
    assert not report.ok
    assert report.unchecked
    assert "part 2" in report.unchecked[0]


# --- no two parts share interior volume -----------------------------------


def test_overlapping_parts_are_caught():
    report = evaluate_assembly(_m(overlaps=[[0, 1, 12.5]]), SPEC)
    assert not report.ok
    assert "part 1" in report.failures[0] and "part 2" in report.failures[0]
    assert "12.5" in report.failures[0]


def test_parts_that_merely_touch_are_not_caught():
    # Touching faces intersect at zero volume. The measurement side reports the
    # number it found; the line between touching and interpenetrating is drawn
    # here, and it must not move onto touching.
    report = evaluate_assembly(_m(overlaps=[[0, 1, 0.0]], gaps=[[0, 1, 0.0]]), SPEC)
    assert report.ok


def test_a_shared_volume_of_pure_boolean_noise_is_not_an_overlap():
    report = evaluate_assembly(_m(overlaps=[[0, 1, 1e-12]], gaps=[[0, 1, 0.0]]), SPEC)
    assert report.ok


# --- the parts mate into one connected assembly ---------------------------


def test_a_floating_part_is_caught():
    report = evaluate_assembly(
        _m(
            part_bboxes=[[50.0, 50.0, 50.0]] * 3,
            gaps=[[0, 1, 0.2], [0, 2, 80.0], [1, 2, 90.0]],
            order=[0, 1, 2],
        ),
        SPEC,
    )
    assert not report.ok
    assert any("part 3" in f for f in report.failures)


def test_a_split_into_two_independent_groups_is_caught():
    report = evaluate_assembly(
        _m(
            part_bboxes=[[50.0, 50.0, 50.0]] * 4,
            gaps=[[0, 1, 0.2], [2, 3, 0.2], [0, 2, 70.0], [0, 3, 70.0], [1, 2, 70.0], [1, 3, 70.0]],
            order=[0, 1, 2, 3],
        ),
        SPEC,
    )
    assert not report.ok
    assert any("two groups" in f or "separate groups" in f for f in report.failures)


def test_a_joint_sitting_at_exactly_the_clearance_is_mated():
    # The prompt tells the model to leave the clearance on every mating face, so a
    # correct joint's closest approach *is* the clearance. A threshold at or below
    # it would refuse every correct split.
    report = evaluate_assembly(_m(gaps=[[0, 1, SPEC.clearance_mm]]), SPEC)
    assert report.ok


def test_a_pair_missing_from_the_gap_table_is_treated_as_not_mated():
    report = evaluate_assembly(_m(gaps=[]), SPEC)
    assert not report.ok


def test_the_report_carries_the_joint_graph_it_judged_connectivity_from():
    report = evaluate_assembly(
        _m(
            part_bboxes=[[50.0, 50.0, 50.0]] * 3,
            gaps=[[0, 1, 0.2], [1, 2, 0.2], [0, 2, 60.0]],
            order=[0, 1, 2],
        ),
        SPEC,
    )
    assert report.ok
    assert report.joints == {0: [1], 1: [0, 2], 2: [1]}


def test_the_joints_and_the_verdict_move_together_when_a_pair_drifts_apart():
    # The guide states which parts join which, and the refusal states that a part
    # joins nothing. Both must read one answer: if widening the gap removed the
    # edge without producing the failure, or the reverse, the two have drifted.
    far = mating_tolerance(SPEC.clearance_mm) * 2
    joined = evaluate_assembly(_m(gaps=[[0, 1, SPEC.clearance_mm]]), SPEC)
    apart = evaluate_assembly(_m(gaps=[[0, 1, far]]), SPEC)

    assert joined.joints == {0: [1], 1: [0]}
    assert joined.ok
    assert apart.joints == {0: [], 1: []}
    assert not apart.ok


def test_a_single_part_has_no_joint_graph():
    # Fewer than two parts leaves the evaluation before any check runs, so there
    # is no graph -- which is not the same as an assembly whose parts turned out
    # to touch nothing, and that one reports an empty edge list per part.
    report = evaluate_assembly(_m(part_bboxes=[[10.0, 10.0, 10.0]], gaps=[], order=[0]), SPEC)
    assert report.joints is None


# --- an assembly order exists ---------------------------------------------


def test_a_trapped_part_is_caught_and_named():
    report = evaluate_assembly(_m(order=None, trapped=[1]), SPEC)
    assert not report.ok
    assert any("part 2" in f for f in report.failures)


def test_no_order_and_no_named_trapped_part_still_refuses():
    report = evaluate_assembly(_m(order=None, trapped=[]), SPEC)
    assert not report.ok


# --- fail closed ----------------------------------------------------------


def test_an_unchecked_entry_alone_makes_the_report_not_ok():
    report = evaluate_assembly(_m(unchecked=["assembly order (work budget spent)"]), SPEC)
    assert not report.ok
    assert report.failures == []
    signal = report.repair_signal()
    assert signal is not None
    assert "could not be established" in signal


def test_the_signal_distinguishes_a_failed_check_from_an_unrunnable_one():
    report = evaluate_assembly(
        _m(overlaps=[[0, 1, 5.0]], unchecked=["assembly order (work budget spent)"]), SPEC
    )
    signal = report.repair_signal()
    assert signal is not None
    assert "could not be established" in signal
    # The overlap finding is a check that ran and failed, so its line must not be
    # dressed as one that could not run.
    assert len(report.failures) == 1
    finding = report.failures[0]
    finding_line = next(line for line in signal.splitlines() if finding in line)
    assert "could not be established" not in finding_line


def test_every_failure_reaches_the_signal():
    report = evaluate_assembly(
        _m(part_bboxes=[[100.0, 100.0, 50.0], [250.0, 40.0, 30.0]], overlaps=[[0, 1, 5.0]]),
        SPEC,
    )
    signal = report.repair_signal()
    assert signal is not None
    for failure in report.failures:
        assert failure in signal


# --- the payload adapter --------------------------------------------------


def test_measurements_from_a_missing_payload_is_none():
    assert AssemblyMeasurements.from_payload(None) is None


def test_measurements_from_a_payload_missing_keys_does_not_raise():
    m = AssemblyMeasurements.from_payload({})
    assert m is not None
    assert m.part_bboxes == []
    assert m.order is None


def test_measurements_from_a_payload_with_an_unknown_key_does_not_raise():
    # The neighbouring RepairContext(**rc) raises TypeError here, which turns an
    # api/worker version skew into a broken call rather than a degraded read.
    m = AssemblyMeasurements.from_payload({"order": [0, 1], "invented_later": 7})
    assert m is not None
    assert m.order == [0, 1]


def test_report_defaults_are_a_passing_report():
    assert AssemblyReport().ok


# --- entries that cannot be read ------------------------------------------


def test_an_unreadable_overlap_entry_refuses_rather_than_being_skipped():
    report = evaluate_assembly(_m(overlaps=[["first", 1, 5.0]]), SPEC)
    assert not report.ok
    assert any("overlap" in reason for reason in report.unchecked)


def test_an_unreadable_gap_entry_refuses_rather_than_being_skipped():
    report = evaluate_assembly(_m(gaps=[[0, 1, 0.2], [0, "second", 1.0]]), SPEC)
    assert not report.ok
    assert any("gap" in reason for reason in report.unchecked)


def test_a_short_pair_entry_is_unreadable_too():
    report = evaluate_assembly(_m(overlaps=[[0, 1]]), SPEC)
    assert not report.ok
    assert report.unchecked


def test_a_gap_naming_a_part_that_does_not_exist_refuses():
    # Out of range rather than unreadable: the entry parses, it just refers to
    # nothing. That means the gaps table disagrees with the part list, which is
    # the same corruption the unreadable case refuses -- ignoring it would let a
    # table that is short or misaligned read as fully measured.
    report = evaluate_assembly(_m(gaps=[[0, 1, 0.2], [0, 99, 0.2]]), SPEC)
    assert not report.ok
    assert any("does not exist" in reason for reason in report.unchecked)


def test_an_overlap_naming_a_part_that_does_not_exist_refuses():
    report = evaluate_assembly(_m(overlaps=[[0, 99, 5.0]]), SPEC)
    assert not report.ok
    assert any("does not exist" in reason for reason in report.unchecked)


# --- the payload shapes a version skew can produce ------------------------


@pytest.mark.parametrize("bad_order", ["unknown", {"a": 1}, [0, "one"], [True, False]])
def test_an_order_that_is_not_a_list_of_indices_refuses(bad_order):
    # Every other field is normalised through list(...); order was taken verbatim
    # and a string read as "an order was found", which is a fail-open on exactly
    # the skew from_payload exists to survive.
    m = AssemblyMeasurements.from_payload(
        {
            "part_bboxes": [[10.0, 10.0, 10.0], [10.0, 10.0, 10.0]],
            "gaps": [[0, 1, 0.2]],
            "order": bad_order,
        }
    )
    report = evaluate_assembly(m, SPEC)
    assert not report.ok


def test_an_order_that_is_not_every_part_refuses():
    report = evaluate_assembly(_m(order=[]), SPEC)
    assert not report.ok
    assert report.order is None
    report = evaluate_assembly(_m(order=[0]), SPEC)
    assert not report.ok


def test_a_trapped_list_of_the_wrong_shape_does_not_raise():
    # evaluate_assembly is documented as never raising, and it is called
    # unguarded from the pipeline -- so a malformed payload must refuse the build
    # rather than take the whole turn down with it.
    m = AssemblyMeasurements.from_payload(
        {
            "part_bboxes": [[10.0, 10.0, 10.0], [10.0, 10.0, 10.0]],
            "gaps": [[0, 1, 0.2]],
            "trapped": ["left"],
        }
    )
    report = evaluate_assembly(m, SPEC)
    assert not report.ok


def test_an_unrunnable_order_search_is_not_reported_as_a_definite_no():
    # A completed search that finds nothing always names the parts it could not
    # free, so no trapped set means the search never finished. Saying "no
    # assembly order exists" there sends the model to fix geometry that was never
    # measured -- and it was the first line of the repair prompt.
    report = evaluate_assembly(
        _m(order=None, trapped=[], unchecked=["assembly order: the search ran out of budget"]),
        SPEC,
    )
    assert not report.ok
    assert report.failures == []
    signal = report.repair_signal()
    assert "no assembly order exists" not in signal


def test_no_order_no_reason_still_refuses():
    report = evaluate_assembly(_m(order=None, trapped=[]), SPEC)
    assert not report.ok
    assert any("no reason" in reason for reason in report.unchecked)


def test_unchecked_survives_a_payload_with_nothing_measured():
    m = AssemblyMeasurements.from_payload(
        {"part_bboxes": [], "unchecked": ["nothing was measured"]}
    )
    report = evaluate_assembly(m, SPEC)
    assert not report.ok


# --- the coupling this module's guard exists for --------------------------


def test_print_fit_still_stays_silent_on_a_box_it_cannot_read():
    # Not a test of this module, but of the reason it guards its own call site:
    # too_big_for answers "" both for "fits" and for "unreadable", so reading its
    # silence as a pass would present an unchecked part as a checked one. Should
    # print_fit ever stop failing open -- it is a known follow-up -- this goes red
    # and points at the guard that can then be simplified.
    from cadless.print_fit import too_big_for

    assert too_big_for(["not a number", 10.0, 10.0], SPEC.volume) == ""
    assert too_big_for([float("nan"), 10.0, 10.0], SPEC.volume) == ""
    assert too_big_for(None, SPEC.volume) == ""
