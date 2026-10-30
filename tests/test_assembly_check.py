"""Assembly post-condition policy tests.

These exercise the pure evaluation function with synthetic measurements (part
boxes, pairwise overlap volumes and gaps, an assembly order), so they need no
live OCCT. The geometry that produces those numbers is tested in
``tests/test_worker.py``.
"""

import json
import math

import pytest

from cadless.assembly_check import (
    PARTS_CHECKS,
    AssemblyMeasurements,
    AssemblyReport,
    evaluate_assembly,
    evaluate_lone_part,
    evaluate_requested_size,
    mating_graph,
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
    # against the build volume is evaluate_lone_part's job on a turn that asked
    # and print_fit's otherwise, not evaluate_assembly's.
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


def test_a_refusal_for_size_names_the_cuts_each_exceeded_axis_needs():
    # A 12 x 250 x 400 panel on a 210 x 200 x 195 bed: the depth needs two pieces
    # and the height three, the width none. "Split the model into more parts" left
    # the model free to cut the axis it had already cut.
    report = evaluate_assembly(_m(part_bboxes=[[100.0, 100.0, 50.0], [12.0, 250.0, 400.0]]), SPEC)
    assert not report.ok
    why = report.failures[0]
    assert "2 along its depth" in why
    assert "3 along its height" in why
    assert "along its width" not in why


def test_a_refusal_for_size_says_not_to_shrink():
    # The other answer the old wording left open: a smaller model that fits.
    report = evaluate_assembly(_m(part_bboxes=[[100.0, 100.0, 50.0], [12.0, 250.0, 400.0]]), SPEC)
    assert "do not shrink" in report.failures[0]


def test_the_cuts_count_the_quarter_turn_the_fit_check_allows():
    # 150 x 205 x 400 fits the footprint turned a quarter, so only the height needs
    # cutting; counted as it stands it would be told to cut the depth as well, a
    # cut the fit it is held to never asked for.
    report = evaluate_assembly(_m(part_bboxes=[[100.0, 100.0, 50.0], [150.0, 205.0, 400.0]]), SPEC)
    why = report.failures[0]
    assert "3 along its height" in why
    assert "along its depth" not in why
    assert "turned a quarter" in why


def test_a_side_exactly_the_bed_needs_no_cut_and_no_turn():
    # 210 wide on a 210 bed as it stands; turned it would be 210 against 200.
    report = evaluate_assembly(_m(part_bboxes=[[100.0, 100.0, 50.0], [210.0, 12.0, 400.0]]), SPEC)
    why = report.failures[0]
    assert "3 along its height" in why
    assert "along its width" not in why
    assert "turned" not in why


# --- a build that came back as one solid ------------------------------------


def test_a_lone_part_larger_than_the_bed_is_refused_with_its_cuts():
    # A model the turn asked to split, returned whole: 300 x 250 x 18 needs two
    # pieces along its width and two along its depth, turned or not.
    report = evaluate_lone_part((300.0, 250.0, 18.0), SPEC)
    assert not report.ok
    why = report.failures[0]
    assert "one part" in why
    assert "300 x 250 x 18" in why
    assert "2 along its width and 2 along its depth" in why
    assert "do not shrink" in why


def test_a_lone_part_that_fits_passes():
    assert evaluate_lone_part((100.0, 100.0, 50.0), SPEC).ok


def test_a_lone_part_with_an_unreadable_box_is_unchecked_rather_than_passed():
    report = evaluate_lone_part(None, SPEC)
    assert not report.ok
    assert report.failures == []
    assert report.unchecked


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


# --- the whole build keeps the size the request gave ------------------------

SIZED = AssemblySpec(
    volume=BuildVolume(210.0, 200.0, 195.0), clearance_mm=0.2, requested_mm=(300.0, 250.0, 18.0)
)


def test_a_build_shrunk_from_the_requested_size_is_refused():
    # A board asked for at 300 x 250 x 18, handed back as one part that fits the
    # bed: every check of the parts passes it, and it is not what was asked for.
    report = evaluate_requested_size((200.0, 200.0, 18.0), SIZED)
    assert not report.ok
    why = report.failures[0]
    assert "200 x 200 x 18" in why
    assert "300 x 250 x 18" in why
    assert "never by shrinking or enlarging" in why


def test_a_build_thicker_than_the_request_is_refused():
    assert not evaluate_requested_size((300.0, 250.0, 36.0), SIZED).ok


def test_the_requested_size_is_kept_in_any_orientation():
    # Which way the model lies is the split's business; the size is not.
    assert evaluate_requested_size((18.0, 300.0, 250.0), SIZED).ok


@pytest.mark.parametrize(
    "bbox, kept",
    [
        ((300.0, 250.0, 18.9), True),
        ((300.0, 250.0, 19.1), False),
        ((302.9, 250.0, 18.0), True),
        ((303.1, 250.0, 18.0), False),
    ],
    ids=[
        "a thin side within a millimetre",
        "a thin side past it",
        "a long side within one percent",
        "a long side past it",
    ],
)
def test_the_slack_is_a_millimetre_or_one_percent_whichever_is_larger(bbox, kept):
    assert evaluate_requested_size(bbox, SIZED).ok is kept


def test_the_requested_size_is_judged_in_millimetres():
    # The worker's box is in authoring units; the same board authored in metres.
    assert evaluate_requested_size((0.3, 0.25, 0.018), SIZED, export_scale=1000.0).ok


def test_a_build_whose_box_cannot_be_read_is_unchecked_rather_than_passed():
    report = evaluate_requested_size((300.0, float("nan"), 18.0), SIZED)
    assert not report.ok
    assert report.failures == []
    assert report.unchecked


@pytest.mark.parametrize("requested", [(300.0, 0.0, 18.0), (300.0, 250.0)])
def test_a_requested_size_that_cannot_be_read_is_unchecked_rather_than_passed(requested):
    spec = AssemblySpec(volume=SPEC.volume, clearance_mm=0.2, requested_mm=requested)
    report = evaluate_requested_size((300.0, 250.0, 18.0), spec)
    assert not report.ok
    assert report.failures == []
    assert report.unchecked


def test_a_turn_that_gave_no_size_is_held_to_none():
    report = evaluate_requested_size((1.0, 1.0, 1.0), SPEC)
    assert (report.failures, report.unchecked) == ([], [])


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


def test_a_tab_s_worth_of_shared_volume_is_blamed_on_the_socket():
    # A thousand cubic millimetres is a whole tab sitting in solid material: the
    # socket was cut outside the receiving part, on the wrong face, or not at
    # all. Telling the model to widen a clearance there sent it the wrong way.
    report = evaluate_assembly(_m(overlaps=[[0, 1, 1000.0]]), SPEC)
    why = report.failures[0]
    assert "a tab sitting in solid material" in why
    assert why.endswith(
        "subtract the socket, the tab's shape enlarged by the clearance on every side, "
        "from the receiving part at the place the tab occupies when assembled"
    )
    assert "widen" not in why


def test_a_sliver_of_shared_volume_is_blamed_on_a_socket_that_does_not_clear_its_tab():
    # A socket cut to the tab's exact size only touches it, and touching measures
    # zero: a sliver is a socket narrower than the tab or off its place, and the
    # fix is the same construction as for a whole tab, not a wider clearance.
    report = evaluate_assembly(_m(overlaps=[[0, 1, 5.0]]), SPEC)
    why = report.failures[0]
    assert "narrower than its tab or off its place" in why
    assert "subtract the socket" in why
    assert "sitting in solid material" not in why


def test_the_line_between_a_sliver_and_a_tab_is_fifty_cubic_millimetres():
    at_the_line = evaluate_assembly(_m(overlaps=[[0, 1, 50.0]]), SPEC).failures[0]
    just_below = evaluate_assembly(_m(overlaps=[[0, 1, 49.9]]), SPEC).failures[0]
    assert "a tab sitting in solid material" in at_the_line
    assert "narrower than its tab" in just_below


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
    assert report.joints == [[1], [0, 2], [1]]


def test_the_joints_and_the_verdict_move_together_when_a_pair_drifts_apart():
    # The guide states which parts join which, and the refusal states that a part
    # joins nothing. Both must read one answer: if widening the gap removed the
    # edge without producing the failure, or the reverse, the two have drifted.
    far = mating_tolerance(SPEC.clearance_mm) * 2
    joined = evaluate_assembly(_m(gaps=[[0, 1, SPEC.clearance_mm]]), SPEC)
    apart = evaluate_assembly(_m(gaps=[[0, 1, far]]), SPEC)

    assert joined.joints == [[1], [0]]
    assert joined.ok
    assert apart.joints == [[], []]
    assert not apart.ok


def test_each_part_s_neighbours_come_back_in_order():
    # The guide names a part's neighbours in the order they are listed, so an
    # unsorted list makes a sentence's wording depend on set iteration. Nine
    # parts, because that is where the two disagree: a small-int set iterates in
    # value order until the table grows, so `list({1, 8})` is `[8, 1]` while a
    # smaller pair would come back sorted either way and prove nothing.
    ring = _m(
        part_bboxes=[[10.0, 10.0, 10.0]] * 9,
        gaps=[[i, i + 1, 0.2] for i in range(8)] + [[0, 8, 0.2]],
        order=list(range(9)),
    )
    report = evaluate_assembly(ring, SPEC)

    assert report.ok
    assert report.joints is not None
    assert report.joints[0] == [1, 8]


def test_the_joints_survive_the_trip_to_the_model_unchanged():
    # The verdict is handed to the orchestrator as JSON, and json.dumps turns an
    # integer key into a string without saying so. A graph keyed by part index
    # would therefore come back a different type on the side that reads it.
    report = evaluate_assembly(_m(), SPEC)
    assert report.joints is not None
    assert json.loads(json.dumps(report.joints)) == report.joints


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


def test_measurements_from_a_payload_missing_releases_degrades_to_empty():
    # A worker too old to record them is the ordinary case for one deploy, and it
    # must read as "not measured" rather than break the call.
    m = AssemblyMeasurements.from_payload({"order": [0, 1]})
    assert m is not None
    assert m.releases == []


def test_measurements_carry_one_release_direction_per_part():
    m = AssemblyMeasurements.from_payload(
        {"order": [1, 0], "releases": [[0.0, 0.0, 1.0], [0.0, 0.0, -1.0]]}
    )
    assert m is not None
    assert m.releases == [[0.0, 0.0, 1.0], [0.0, 0.0, -1.0]]


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


# --- values that are not measurements -------------------------------------
#
# No intersection or distance the kernel measures is negative or unbounded, so
# such a value was never measured, whoever sent it. Held to a threshold, NaN
# and the infinities read as a pass or a fault that no measurement gave.

_NOT_MEASURED = [math.nan, math.inf, -1.0]
_NOT_MEASURED_IDS = ["nan", "inf", "negative"]


def _only(reasons, prefix):
    return [reason for reason in reasons if reason.startswith(prefix)]


@pytest.mark.parametrize("volume", _NOT_MEASURED, ids=_NOT_MEASURED_IDS)
def test_a_shared_volume_that_was_not_measured_is_unchecked_naming_the_pair(volume):
    report = evaluate_assembly(_m(overlaps=[[0, 1, volume]]), SPEC)
    assert report.failures == []
    assert len(_only(report.unchecked, "shared volume between part 1 and part 2")) == 1


def test_a_build_nobody_asked_to_join_is_held_to_the_same_overlap_rule():
    report = evaluate_assembly(_m(overlaps=[[0, 1, math.nan]]), SPEC, checks=PARTS_CHECKS)
    assert report.failures == []
    assert len(_only(report.unchecked, "shared volume between part 1 and part 2")) == 1


@pytest.mark.parametrize("distance", _NOT_MEASURED, ids=_NOT_MEASURED_IDS)
def test_a_gap_that_was_not_measured_is_unchecked_naming_the_pair(distance):
    # Not a part that "comes no closer" to the others: that sends the repair
    # after a joint that may be sound, and a negative distance read as joined.
    report = evaluate_assembly(_m(gaps=[[0, 1, distance]]), SPEC)
    assert report.failures == []
    assert len(_only(report.unchecked, "gap between part 1 and part 2")) == 1


@pytest.mark.parametrize(
    "table, prefix",
    [("overlaps", "shared volume between shelf and leg"), ("gaps", "gap between shelf and leg")],
)
def test_a_value_that_was_not_measured_names_the_parts_as_their_build_did(table, prefix):
    report = evaluate_assembly(_m(**{table: [[0, 1, math.nan]]}), SPEC, names=["shelf", "leg"])
    assert _only(report.unchecked, prefix)


def test_the_joints_described_are_the_measured_ones_only():
    # A build nobody asked to join is not judged on its joints, so what it
    # describes is the whole of it: a distance never measured is no joint.
    report = evaluate_assembly(_m(gaps=[[0, 1, -math.inf]]), SPEC, checks=PARTS_CHECKS)
    assert report.joints == [[], []]


def test_a_pair_missing_from_the_gap_table_is_not_measured_rather_than_far_apart():
    report = evaluate_assembly(_m(gaps=[]), SPEC)
    assert report.failures == []
    assert _only(report.unchecked, "parts mating into one assembly")


def test_a_part_whose_only_unmeasured_gap_could_join_it_is_not_called_loose():
    report = evaluate_assembly(
        _m(
            part_bboxes=[[50.0, 50.0, 50.0]] * 3,
            gaps=[[0, 1, 0.2], [1, 2, math.nan], [0, 2, 80.0]],
            order=[0, 1, 2],
        ),
        SPEC,
    )
    assert report.failures == []


def test_the_distance_that_could_join_the_parts_is_named_as_not_measured():
    report = evaluate_assembly(
        _m(
            part_bboxes=[[50.0, 50.0, 50.0]] * 3,
            gaps=[[0, 1, 0.2], [1, 2, math.nan], [0, 2, 80.0]],
            order=[0, 1, 2],
        ),
        SPEC,
    )
    assert _only(report.unchecked, "parts mating into one assembly") == [
        "parts mating into one assembly: no distance was measured between part 2 and part 3"
    ]


@pytest.mark.parametrize(
    "gaps, line",
    [([], "shelf and leg, and 2 other pairs"), ([[0, 1, 0.2]], "shelf and top, and 1 other pair")],
    ids=["several", "one-other"],
)
def test_the_line_counts_the_other_unmeasured_pairs_in_the_build_s_names(gaps, line):
    report = evaluate_assembly(
        _m(part_bboxes=[[50.0, 50.0, 50.0]] * 3, gaps=gaps, order=[0, 1, 2]),
        SPEC,
        names=["shelf", "leg", "top"],
    )
    assert _only(report.unchecked, "parts mating into one assembly") == [
        f"parts mating into one assembly: no distance was measured between {line}"
    ]


def test_the_joints_described_on_a_judged_build_are_the_measured_ones():
    # A negative distance, which reads as a joint when taken at its word.
    report = evaluate_assembly(
        _m(
            part_bboxes=[[50.0, 50.0, 50.0]] * 3,
            gaps=[[0, 1, 0.2], [1, 2, -math.inf], [0, 2, 80.0]],
            order=[0, 1, 2],
        ),
        SPEC,
    )
    assert report.joints == [[1], [0], []]


def test_parts_measured_apart_from_each_other_may_still_join_through_the_rest():
    # Only parts 1 and 2 were measured, far apart; part 3 could join either.
    report = evaluate_assembly(
        _m(part_bboxes=[[50.0, 50.0, 50.0]] * 3, gaps=[[0, 1, 80.0]], order=[0, 1, 2]), SPEC
    )
    assert report.failures == []


def test_a_part_listed_against_itself_is_no_pair_between_groups():
    report = evaluate_assembly(_m(gaps=[[0, 1, 0.2], [0, 0, 0.0]]), SPEC)
    assert report.ok


def _one_loose_one_unknown():
    # Parts 1 and 3 joined; part 2 measured far from every other; part 4 never
    # measured against 1 or 3, so it may yet join them.
    return evaluate_assembly(
        _m(
            part_bboxes=[[50.0, 50.0, 50.0]] * 4,
            gaps=[[0, 2, 0.2], [0, 1, 80.0], [1, 2, 80.0], [1, 3, 80.0]],
            order=[0, 1, 2, 3],
        ),
        SPEC,
    )


def test_a_part_measured_apart_beside_one_nobody_measured_is_still_loose():
    report = _one_loose_one_unknown()
    assert [f.split(" comes no closer")[0] for f in report.failures] == ["part 2"]


def test_the_unmeasured_pair_is_named_beside_a_loose_part():
    report = _one_loose_one_unknown()
    assert _only(report.unchecked, "parts mating into one assembly") == [
        "parts mating into one assembly: no distance was measured between part 1 and part 4, "
        "and 1 other pair"
    ]


def test_an_unmeasured_gap_inside_a_joined_group_leaves_the_mating_established():
    report = evaluate_assembly(
        _m(
            part_bboxes=[[50.0, 50.0, 50.0]] * 3,
            gaps=[[0, 1, 0.2], [1, 2, 0.2], [0, 2, math.nan]],
            order=[0, 1, 2],
        ),
        SPEC,
    )
    assert _only(report.unchecked, "parts mating into one assembly") == []


def test_a_part_measured_far_from_every_other_is_still_loose():
    report = evaluate_assembly(
        _m(
            part_bboxes=[[50.0, 50.0, 50.0]] * 3,
            gaps=[[0, 1, math.nan], [0, 2, 80.0], [1, 2, 90.0]],
            order=[0, 1, 2],
        ),
        SPEC,
    )
    assert [f.split(" comes no closer")[0] for f in report.failures] == ["part 3"]


def test_groups_an_unmeasured_gap_could_join_are_not_called_separate():
    report = evaluate_assembly(
        _m(
            part_bboxes=[[50.0, 50.0, 50.0]] * 4,
            gaps=[[0, 1, 0.2], [2, 3, 0.2], [1, 2, math.inf], [0, 2, 70.0], [0, 3, 70.0]]
            + [[1, 3, 70.0]],
            order=[0, 1, 2, 3],
        ),
        SPEC,
    )
    assert report.failures == []


def test_groups_no_unmeasured_gap_could_join_are_still_separate():
    # The pair left unmeasured sits inside one group; every pair across the
    # two was measured far apart.
    report = evaluate_assembly(
        _m(
            part_bboxes=[[50.0, 50.0, 50.0]] * 4,
            gaps=[[0, 1, math.nan], [2, 3, 0.2], [0, 2, 80.0], [0, 3, 80.0]]
            + [[1, 2, 80.0], [1, 3, 80.0]],
            order=[0, 1, 2, 3],
        ),
        SPEC,
    )
    assert any("separate groups" in f for f in report.failures)


def test_the_joint_graph_still_answers_the_joints_and_what_could_not_be_read():
    # A public name: its answer keeps its two parts.
    neighbours, problems = mating_graph([[0, 1, 0.2], [0, 1, math.nan]], 2, 1.0)
    assert (neighbours, len(problems)) == ({0: {1}, 1: {0}}, 1)


@pytest.mark.parametrize("table", ["overlaps", "gaps"])
def test_a_part_index_that_is_infinite_is_an_unreadable_entry_not_a_raise(table):
    entries = {"overlaps": [[math.inf, 1, 5.0]], "gaps": [[0, 1, 0.2], [0, math.inf, 0.2]]}
    report = evaluate_assembly(_m(**{table: entries[table]}), SPEC)
    assert any("unreadable entry" in reason for reason in report.unchecked)


def test_a_box_too_large_to_be_a_number_is_unchecked_not_a_raise():
    report = evaluate_assembly(_m(part_bboxes=[[10**400, 10.0, 10.0], [10.0, 10.0, 10.0]]), SPEC)
    assert _only(report.unchecked, "build volume fit for part 1")


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


# --- the parts checks: a build that did not ask to be an assembly -----------


def test_the_parts_checks_do_not_hold_a_part_to_the_bed():
    """Fitting the bed is print_fit's question for a build nobody asked to split."""
    report = evaluate_assembly(
        _m(part_bboxes=[[999.0, 100.0, 50.0], [100.0, 100.0, 50.0]]), SPEC, checks=PARTS_CHECKS
    )
    assert report.ok


def test_the_parts_checks_let_parts_stand_apart():
    """An exploded view, or a set of separate objects, is not a failed joint."""
    report = evaluate_assembly(_m(gaps=[[0, 1, 30.0]]), SPEC, checks=PARTS_CHECKS)
    assert report.ok


def test_the_parts_checks_still_describe_the_joints():
    report = evaluate_assembly(_m(), SPEC, checks=PARTS_CHECKS)
    assert report.joints == [[1], [0]]


def test_the_parts_checks_catch_two_parts_in_one_space():
    report = evaluate_assembly(_m(overlaps=[[0, 1, 40.0]]), SPEC, checks=PARTS_CHECKS)
    assert not report.ok
    assert any("share" in f for f in report.failures)


@pytest.mark.parametrize("shared", [1000.0, 5.0])
def test_the_parts_checks_tell_a_build_nobody_asked_to_join_only_to_trim_the_parts_apart(shared):
    # No seam, tab or clearance was ever named to such a build, so the sentence
    # can lean on none of them, on either side of the line.
    report = evaluate_assembly(_m(overlaps=[[0, 1, shared]]), SPEC, checks=PARTS_CHECKS)
    why = report.failures[0]
    assert "so they only touch" in why
    assert "socket" not in why and "clearance" not in why


def test_the_parts_checks_let_a_captive_part_stay():
    """A chain link or a ball in a cage cannot come out, and is meant not to."""
    report = evaluate_assembly(_m(order=None, trapped=[0, 1]), SPEC, checks=PARTS_CHECKS)
    assert report.ok


def test_an_unreadable_gap_entry_does_not_fail_the_parts_checks():
    report = evaluate_assembly(_m(gaps=[["x"]]), SPEC, checks=PARTS_CHECKS)
    assert report.ok


@pytest.mark.parametrize(
    "reason",
    [
        "gap between part 1 and part 2: the distance could not be computed",
        "assembly order: the search ran out of budget after 9 probes",
    ],
)
def test_a_measurement_of_a_check_not_faced_does_not_fail_the_parts_checks(reason):
    report = evaluate_assembly(_m(unchecked=[reason]), SPEC, checks=PARTS_CHECKS)
    assert report.ok
    assert not evaluate_assembly(_m(unchecked=[reason]), SPEC).ok


@pytest.mark.parametrize(
    "reason",
    [
        "shared volume between part 1 and part 2: the intersection could not be computed",
        "overlap and gap between every pair: the measurement ran out of budget after 0 of 1 pairs",
        "something the measurement did not say the check of",
    ],
)
def test_a_measurement_bearing_on_overlap_or_on_nothing_named_fails_the_parts_checks(reason):
    report = evaluate_assembly(_m(unchecked=[reason]), SPEC, checks=PARTS_CHECKS)
    assert not report.ok
    assert report.unchecked == [reason]


def test_a_check_named_wrongly_is_reported_rather_than_skipped():
    report = evaluate_assembly(_m(overlaps=[[0, 1, 40.0]]), SPEC, checks=frozenset({"overlaps"}))
    assert not report.ok
    assert any("overlaps" in reason for reason in report.unchecked)


def test_a_reason_that_is_not_text_counts_against_the_report_and_never_raises():
    report = evaluate_assembly(_m(unchecked=[None]), SPEC)
    assert not report.ok


@pytest.mark.parametrize("checks", [PARTS_CHECKS, None])
def test_an_order_of_other_parts_is_withheld_whatever_is_judged(checks):
    kwargs = {} if checks is None else {"checks": checks}
    report = evaluate_assembly(_m(order=[0, 0]), SPEC, **kwargs)
    assert report.order is None


# --- the parts by the names their build gave them ---------------------------

_NAMES = ["chassis", "wheel_fl"]


def test_a_failure_names_the_part_its_build_named():
    report = evaluate_assembly(_m(order=None, trapped=[1]), SPEC, names=_NAMES)
    assert any("wheel_fl" in f for f in report.failures)
    assert not any("part 2" in f for f in report.failures)


def test_a_shared_volume_names_both_parts():
    report = evaluate_assembly(_m(overlaps=[[0, 1, 40.0]]), SPEC, names=_NAMES)
    assert any("chassis and wheel_fl share" in f for f in report.failures)


def test_a_part_its_build_left_unnamed_is_numbered_from_one():
    report = evaluate_assembly(_m(order=None, trapped=[1]), SPEC, names=["chassis", "part1"])
    assert any("part 2" in f for f in report.failures)


def test_naming_the_parts_changes_the_words_and_nothing_else():
    named = evaluate_assembly(
        _m(overlaps=[[0, 1, 40.0]], order=None, trapped=[1]), SPEC, names=_NAMES
    )
    numbered = evaluate_assembly(_m(overlaps=[[0, 1, 40.0]], order=None, trapped=[1]), SPEC)
    assert (named.ok, named.order, named.joints, len(named.failures)) == (
        numbered.ok,
        numbered.order,
        numbered.joints,
        len(numbered.failures),
    )


def test_two_parts_with_one_name_are_told_apart_by_their_numbers():
    report = evaluate_assembly(_m(overlaps=[[0, 1, 40.0]]), SPEC, names=["wheel", "wheel"])
    assert any("wheel (part 1) and wheel (part 2) share" in f for f in report.failures)


def test_a_part_too_big_for_the_bed_is_named():
    report = evaluate_assembly(
        _m(part_bboxes=[[999.0, 100.0, 50.0], [100.0, 100.0, 50.0]]), SPEC, names=_NAMES
    )
    assert any(f.startswith("chassis is") for f in report.failures)


def test_a_part_that_touches_nothing_is_named():
    report = evaluate_assembly(_m(gaps=[[0, 1, 30.0]]), SPEC, names=_NAMES)
    assert any("chassis, wheel_fl comes no closer" in f for f in report.failures)


def test_a_name_that_reads_as_another_part_s_number_is_told_apart():
    report = evaluate_assembly(_m(overlaps=[[0, 1, 40.0]]), SPEC, names=["part 2", "part1"])
    assert any("part 2 (part 1) and part 2 share" in f for f in report.failures)


@pytest.mark.parametrize("blank", ["   ", "\n"])
def test_a_blank_label_is_no_name(blank):
    report = evaluate_assembly(_m(overlaps=[[0, 1, 40.0]]), SPEC, names=[blank, "lid"])
    assert any(f.startswith("part 1 and lid share") for f in report.failures)


def test_a_label_over_several_lines_is_put_on_one():
    report = evaluate_assembly(_m(overlaps=[[0, 1, 40.0]]), SPEC, names=["front\nwheel", "lid"])
    assert any(f.startswith("front wheel and lid share") for f in report.failures)


def test_a_name_holding_a_bidi_override_is_numbered():
    report = evaluate_assembly(_m(overlaps=[[0, 1, 40.0]]), SPEC, names=["a\u202eb", "lid"])
    assert any(f.startswith("part 1 and lid share") for f in report.failures)


def test_a_name_written_with_a_joiner_is_used():
    name = "\u06a9\u062a\u0627\u0628\u200c\u062e\u0627\u0646\u0647"
    report = evaluate_assembly(_m(overlaps=[[0, 1, 40.0]]), SPEC, names=[name, "lid"])
    assert any(f.startswith(f"{name} and lid share") for f in report.failures)


def test_a_name_with_nothing_visible_is_numbered():
    report = evaluate_assembly(_m(overlaps=[[0, 1, 40.0]]), SPEC, names=["\u200b\u200d", "lid"])
    assert any(f.startswith("part 1 and lid share") for f in report.failures)
