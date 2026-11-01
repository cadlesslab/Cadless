"""Deterministic geometry property assertion tests.

These exercise the pure assertion-evaluation function with synthetic geometry
signatures (bbox/volume/part-count/manifold numbers), so they need no live OCCT.
"""

import math

import pytest

from cadless.assertions import (
    GeometryAssertions,
    GeometrySignature,
    evaluate_assertions,
)


def _sig(**kw) -> GeometrySignature:
    base = dict(
        volume=1000.0, bbox=(10.0, 10.0, 10.0), part_count=1, manifold=True, min_wall_thickness=2.0
    )
    base.update(kw)
    return GeometrySignature(**base)


# --- missing / empty assertion set never blocks ---------------------------


def test_no_assertions_passes_with_no_signals():
    report = evaluate_assertions(_sig(), GeometryAssertions())
    assert report.ok
    assert report.failures == []
    assert report.repair_signal() is None


def test_assertions_none_is_treated_as_empty():
    report = evaluate_assertions(_sig(), None)
    assert report.ok
    assert report.repair_signal() is None


# --- bounding-box extents (with tolerance) ---------------------------------


def test_bbox_within_tolerance_passes():
    a = GeometryAssertions(bbox=(10.0, 10.0, 10.0), bbox_tolerance=0.5)
    report = evaluate_assertions(_sig(bbox=(10.3, 9.8, 10.0)), a)
    assert report.ok
    assert report.repair_signal() is None


def test_bbox_outside_tolerance_fails():
    a = GeometryAssertions(bbox=(10.0, 10.0, 10.0), bbox_tolerance=0.5)
    report = evaluate_assertions(_sig(bbox=(12.0, 10.0, 10.0)), a)
    assert not report.ok
    assert any("bounding box" in f.lower() for f in report.failures)
    signal = report.repair_signal()
    assert signal is not None
    assert "12" in signal and "10" in signal


# --- min wall thickness ----------------------------------------------------


def test_min_wall_thickness_met_passes():
    a = GeometryAssertions(min_wall_thickness=1.0)
    assert evaluate_assertions(_sig(min_wall_thickness=2.0), a).ok


def test_min_wall_thickness_violated_fails():
    a = GeometryAssertions(min_wall_thickness=3.0)
    report = evaluate_assertions(_sig(min_wall_thickness=1.5), a)
    assert not report.ok
    assert any("thickness" in f.lower() for f in report.failures)


def test_min_wall_thickness_unknown_does_not_block():
    # PoC limitation: thickness may be uncomputable. Unknown must never block.
    a = GeometryAssertions(min_wall_thickness=3.0)
    report = evaluate_assertions(_sig(min_wall_thickness=None), a)
    assert report.ok


@pytest.mark.parametrize(
    "bbox",
    [(math.nan, 10.0, 10.0), (10.0, math.inf, 10.0), None, (10.0, 10.0), (10**400, 10.0, 10.0)],
    ids=["nan", "inf", "missing", "short", "too-large"],
)
def test_a_bbox_that_is_not_three_numbers_is_skipped_not_passed(bbox):
    a = GeometryAssertions(bbox=(10.0, 10.0, 10.0), bbox_tolerance=0.5)
    report = evaluate_assertions(_sig(bbox=bbox), a)
    assert (report.failures, report.skipped) == ([], ["bounding box extents (not measured)"])


@pytest.mark.parametrize(
    "thickness",
    [math.nan, math.inf, "thin", 10**400, [1.0], "2.0", True],
    ids=["nan", "inf", "text", "too-large", "list", "text-of-a-number", "flag"],
)
def test_a_wall_thickness_that_is_not_a_number_is_skipped_not_passed(thickness):
    a = GeometryAssertions(min_wall_thickness=3.0)
    report = evaluate_assertions(_sig(min_wall_thickness=thickness), a)
    assert (report.failures, report.skipped) == ([], ["min wall thickness (not measured)"])


# The request's side of each comparison is held to the same rule as the build's:
# a value that is no length is no assertion, and it is skipped rather than
# allowed to pass every build, fail every build, or raise.


@pytest.mark.parametrize(
    "expected",
    [(math.nan, 10.0, 10.0), (10.0, 10.0), (-10.0, 10.0, 10.0)],
    ids=["nan", "short", "negative"],
)
def test_an_expected_size_that_is_not_three_lengths_is_skipped_not_judged(expected):
    report = evaluate_assertions(_sig(), GeometryAssertions(bbox=expected))
    assert report.failures == []
    assert report.skipped


@pytest.mark.parametrize("tolerance", [math.nan, math.inf, -1.0], ids=["nan", "inf", "negative"])
def test_a_tolerance_that_is_no_length_is_skipped_not_judged(tolerance):
    a = GeometryAssertions(bbox=(10.0, 10.0, 10.0), bbox_tolerance=tolerance)
    report = evaluate_assertions(_sig(bbox=(12.0, 10.0, 10.0)), a)
    assert report.failures == []
    assert report.skipped


@pytest.mark.parametrize("minimum", [math.nan, math.inf, -1.0], ids=["nan", "inf", "negative"])
def test_a_minimum_wall_that_is_no_length_is_skipped_not_judged(minimum):
    report = evaluate_assertions(_sig(), GeometryAssertions(min_wall_thickness=minimum))
    assert report.failures == []
    assert report.skipped


# --- manifold / watertight -------------------------------------------------


def test_manifold_expected_and_true_passes():
    a = GeometryAssertions(manifold=True)
    assert evaluate_assertions(_sig(manifold=True), a).ok


def test_manifold_expected_but_false_fails():
    a = GeometryAssertions(manifold=True)
    report = evaluate_assertions(_sig(manifold=False), a)
    assert not report.ok
    assert any("manifold" in f.lower() or "watertight" in f.lower() for f in report.failures)


def test_manifold_unknown_does_not_block():
    a = GeometryAssertions(manifold=True)
    assert evaluate_assertions(_sig(manifold=None), a).ok


# --- expected part count ---------------------------------------------------


def test_part_count_match_passes():
    a = GeometryAssertions(expected_part_count=2)
    assert evaluate_assertions(_sig(part_count=2), a).ok


def test_part_count_mismatch_fails():
    a = GeometryAssertions(expected_part_count=1)
    report = evaluate_assertions(_sig(part_count=3), a)
    assert not report.ok
    assert any("part" in f.lower() for f in report.failures)


def test_a_part_count_that_was_not_computed_is_skipped_not_failed():
    # The worker reports none where it could not count the solids.
    report = evaluate_assertions(_sig(part_count=None), GeometryAssertions(expected_part_count=1))
    assert report.failures == []
    assert report.skipped


@pytest.mark.parametrize("expected", ["2", -1], ids=["text", "negative"])
def test_an_expected_part_count_that_is_not_a_count_is_skipped_not_judged(expected):
    # Compared as it came, it failed every build, the one with exactly that many
    # parts among them.
    report = evaluate_assertions(
        _sig(part_count=2), GeometryAssertions(expected_part_count=expected)
    )
    assert report.failures == []
    assert report.skipped


# --- multiple failures aggregate ------------------------------------------


def test_multiple_failures_aggregate_into_one_signal():
    a = GeometryAssertions(
        bbox=(10.0, 10.0, 10.0),
        bbox_tolerance=0.1,
        expected_part_count=1,
        manifold=True,
    )
    report = evaluate_assertions(_sig(bbox=(20.0, 10.0, 10.0), part_count=5, manifold=False), a)
    assert not report.ok
    assert len(report.failures) == 3
    signal = report.repair_signal()
    assert signal is not None
    # The signal mentions every failed assertion.
    assert "bounding box" in signal.lower()
    assert "part" in signal.lower()
    assert "manifold" in signal.lower()
