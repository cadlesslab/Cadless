"""Deterministic geometry property assertions.

The CAD translation of "run the unit tests": cheap, deterministic post-conditions
attached to a request and checked *after* a successful build. Supported checks:

  * **bounding-box extents** — expected (X, Y, Z) sizes within a tolerance;
  * **min wall thickness** — the thinnest wall must be >= a floor;
  * **manifold / watertight** — the solid must be a single closed manifold shell;
  * **expected part count** — the number of disjoint solids.

A :class:`GeometrySignature` carries the numbers we *can* derive cheaply from the
build (see :func:`cadless._worker_child._summarise`). :func:`evaluate_assertions`
is a pure function: signature + assertion spec in, structured
:class:`AssertionReport` out. It never raises and never blocks — a failed
assertion only yields a human-readable repair signal that the pipeline feeds back
through the *same* repair mechanism the VLM critique uses; a missing
assertion (``None`` field) or an unknown/uncomputable metric simply contributes no
signal.

PoC limitations
---------------
``min_wall_thickness`` and ``manifold`` are only as good as what the executor can
compute. When a metric is genuinely unavailable the signature carries ``None`` and
the corresponding check is *skipped* (never failed) — an assertion must never block
on a value we could not measure. These are skips, not silent always-passes: a
metric we *did* measure and that violates its assertion does fail.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class GeometryAssertions:
    """Optional, additive post-conditions a request may carry (the neutral spec).

    Every field is optional; an unset (``None``) field is simply not checked. The
    whole object is optional too — a request with no assertions is the common case
    and never produces a repair signal.
    """

    # Expected bounding-box extents (X, Y, Z) and the per-axis absolute tolerance.
    bbox: tuple[float, float, float] | None = None
    bbox_tolerance: float = 0.5
    # Minimum acceptable wall thickness (thinnest wall must be >= this).
    min_wall_thickness: float | None = None
    # Whether the result must be a single closed manifold (watertight) solid.
    manifold: bool | None = None
    # Expected number of disjoint solids in the result.
    expected_part_count: int | None = None

    def is_empty(self) -> bool:
        """True when no assertion is set (so evaluation produces no signal)."""
        return (
            self.bbox is None
            and self.min_wall_thickness is None
            and self.manifold is None
            and self.expected_part_count is None
        )


@dataclass(frozen=True)
class GeometrySignature:
    """Cheap, deterministic metrics measured after a successful build.

    ``min_wall_thickness`` and ``manifold`` may be ``None`` when the executor could
    not compute them; assertions against an unknown metric are skipped, not failed.
    """

    volume: float
    bbox: tuple[float, float, float]
    part_count: int
    manifold: bool | None = None
    min_wall_thickness: float | None = None


@dataclass
class AssertionReport:
    """Outcome of evaluating an assertion spec against a geometry signature."""

    failures: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when no assertion failed (skips and passes both count as ok)."""
        return not self.failures

    def repair_signal(self) -> str | None:
        """Human-readable repair prompt fragment, or ``None`` when nothing failed.

        Aggregates every failed assertion into one message so the pipeline can feed
        it through the same repair channel as the VLM critique.
        """
        if not self.failures:
            return None
        bullets = "\n".join(f"- {f}" for f in self.failures)
        return f"The built geometry violates the requested post-conditions:\n{bullets}"


def evaluate_assertions(
    signature: GeometrySignature,
    assertions: GeometryAssertions | None,
) -> AssertionReport:
    """Check ``signature`` against ``assertions``; pure, never raises, never blocks.

    A missing assertion spec (or one with no fields set) yields an empty, passing
    report. Each set assertion is compared against the measured metric; a metric
    that is ``None`` (uncomputable at PoC scale) is skipped rather than failed.
    """
    report = AssertionReport()
    if assertions is None or assertions.is_empty():
        return report

    if assertions.bbox is not None:
        _check_bbox(signature, assertions, report)
    if assertions.expected_part_count is not None:
        _check_part_count(signature, assertions, report)
    if assertions.manifold is not None:
        _check_manifold(signature, assertions, report)
    if assertions.min_wall_thickness is not None:
        _check_thickness(signature, assertions, report)
    return report


def _check_bbox(sig: GeometrySignature, a: GeometryAssertions, report: AssertionReport) -> None:
    tol = a.bbox_tolerance
    expected = a.bbox
    actual = sig.bbox
    deviations = [
        (axis, exp, act)
        for axis, exp, act in zip("XYZ", expected, actual, strict=True)
        if abs(act - exp) > tol
    ]
    if deviations:
        detail = ", ".join(
            f"{axis}: expected {exp:g}, got {act:g}" for axis, exp, act in deviations
        )
        report.failures.append(f"bounding box extents off by more than {tol:g}mm ({detail})")


def _check_part_count(
    sig: GeometrySignature, a: GeometryAssertions, report: AssertionReport
) -> None:
    if sig.part_count != a.expected_part_count:
        report.failures.append(
            f"part count mismatch: expected {a.expected_part_count}, got {sig.part_count}"
        )


def _check_manifold(sig: GeometrySignature, a: GeometryAssertions, report: AssertionReport) -> None:
    if sig.manifold is None:
        report.skipped.append("manifold/watertight (not computed)")
        return
    if a.manifold and not sig.manifold:
        report.failures.append("geometry is not a single watertight manifold solid")
    elif not a.manifold and sig.manifold:
        report.failures.append(
            "geometry is a closed manifold but a non-manifold result was requested"
        )


def _check_thickness(
    sig: GeometrySignature, a: GeometryAssertions, report: AssertionReport
) -> None:
    if sig.min_wall_thickness is None:
        report.skipped.append("min wall thickness (not computed)")
        return
    if sig.min_wall_thickness < a.min_wall_thickness:
        report.failures.append(
            f"min wall thickness {sig.min_wall_thickness:g}mm is below the required "
            f"{a.min_wall_thickness:g}mm"
        )
