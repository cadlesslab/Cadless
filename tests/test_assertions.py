"""Deterministic geometry property assertion tests.

These exercise the pure assertion-evaluation function with synthetic geometry
signatures (bbox/volume/part-count/manifold numbers), so they need no live OCCT.
"""

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
