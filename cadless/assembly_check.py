"""Geometric post-conditions for a multi-part build: does it actually go together?

A turn that asks for an assembly gets its parts from the model, which is the only
participant that knows where a seam does least harm -- and which has no way to
guarantee the result fits, mates, or can be put together. This module is where
that becomes something the engine establishes rather than something the model
claims. Four checks, each able to name itself when it fails:

  * **build volume fit** -- every part printable on its own;
  * **no shared interior volume** -- two parts cannot occupy the same space;
  * **mating** -- the parts form one connected assembly rather than loose pieces;
  * **assembly order** -- an order exists in which each part reaches its place.

The split of work mirrors :mod:`cadless.assertions`: an
:class:`AssemblyMeasurements` carries numbers measured where the solids are live
(inside the worker child), and :func:`evaluate_assembly` is a pure function --
measurements plus the printer's spec in, a structured :class:`AssemblyReport`
out. It never raises. A failure becomes a repair signal fed through the same
channel as the VLM critique.

Failing closed, unlike its neighbour
------------------------------------
:mod:`cadless.assertions` skips a check whose metric could not be measured, so an
unknown never blocks. That is the right policy there and the wrong one here: an
assembly nobody could check must not be handed over as a checked one. A check
that could not be established therefore lands in ``unchecked``, and an
``unchecked`` entry makes the report not ``ok`` exactly as a failure does. The
report keeps the two apart so the reader can tell "checked and wrong" from "could
not be checked".
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from cadless.print_fit import _dimensions, too_big_for
from cadless.printer_profile import AssemblySpec

#: How far apart two parts may sit and still count as mating, as a multiple of the
#: joint clearance.
#:
#: A correct joint's closest approach *is* the clearance: the model is told to cut
#: the socket that much larger than the tab it receives, so the two faces never
#: touch. A threshold at or below the clearance would therefore refuse every
#: correct split. The multiple leaves room for a chamfer or fillet on the mating
#: faces, where the nearest points of two joined parts sit further apart than the
#: nominal gap.
MATING_TOLERANCE_FACTOR = 4.0

#: The floor under that tolerance, in millimetres, so a printer configured with no
#: clearance at all still leaves a usable window rather than demanding contact.
MATING_TOLERANCE_FLOOR_MM = 1.0

#: Below this shared volume a boolean intersection is arithmetic noise rather than
#: interpenetration. Faces that merely touch intersect at exactly zero, and the
#: distinction between touching and overlapping is the line this module draws.
OVERLAP_EPSILON_MM3 = 1e-9


@dataclass(frozen=True)
class AssemblyMeasurements:
    """What the executor measured about the relations between parts.

    Every field is a JSON primitive, because this travels through the worker's
    stdout payload and, on the remote path, through ``asdict`` and back.

    Pair entries are ``[i, j, value]`` with part indices into ``part_bboxes``:
    ``overlaps`` carries the shared volume of pairs that intersect at all, and
    ``gaps`` the closest approach of every pair. ``order`` is an order in which
    the parts can be brought together, or ``None`` when none was found, in which
    case ``trapped`` names the parts that could not be freed.
    """

    part_bboxes: list[list[float]] = field(default_factory=list)
    overlaps: list[list[float]] = field(default_factory=list)
    gaps: list[list[float]] = field(default_factory=list)
    order: list[int] | None = None
    trapped: list[int] = field(default_factory=list)
    unchecked: list[str] = field(default_factory=list)

    @classmethod
    def from_payload(cls, data: object) -> AssemblyMeasurements | None:
        """Rebuild from a worker payload, or ``None`` when there is nothing to read.

        Key by key rather than ``cls(**data)``: an api and a worker running
        different engine builds disagree about the field set, and unpacking turns
        that skew into a ``TypeError`` that fails the whole call. Read this way an
        older reader ignores a field it does not know and a newer one falls back
        to the defaults, which degrades instead of breaking.
        """
        if not isinstance(data, dict):
            return None
        return cls(
            part_bboxes=list(data.get("part_bboxes") or []),
            overlaps=list(data.get("overlaps") or []),
            gaps=list(data.get("gaps") or []),
            order=data.get("order"),
            trapped=list(data.get("trapped") or []),
            unchecked=list(data.get("unchecked") or []),
        )


@dataclass
class AssemblyReport:
    """Outcome of checking one build's parts against the printer they are for."""

    failures: list[str] = field(default_factory=list)
    unchecked: list[str] = field(default_factory=list)
    order: list[int] | None = None

    @property
    def ok(self) -> bool:
        """True only when every check ran *and* passed.

        An ``unchecked`` entry counts against it. That is the whole difference
        between this and :class:`cadless.assertions.AssertionReport`, and the
        reason is in the module docstring.
        """
        return not self.failures and not self.unchecked

    def repair_signal(self) -> str | None:
        """One repair prompt naming everything wrong, or ``None`` when nothing is.

        Aggregated into a single message so the pipeline can feed it through the
        same channel as the VLM critique.
        """
        if self.ok:
            return None
        bullets = [f"- {failure}" for failure in self.failures]
        bullets += [f"- could not be established: {reason}" for reason in self.unchecked]
        return "The parts of this assembly were not accepted:\n" + "\n".join(bullets)


def evaluate_assembly(
    measurements: AssemblyMeasurements | None,
    spec: AssemblySpec,
) -> AssemblyReport:
    """Check ``measurements`` against ``spec``; pure, and never raises.

    A build with nothing measured, or with fewer than two parts, has no relation
    to check and yields an empty passing report -- judging a lone part against the
    build volume belongs to :mod:`cadless.print_fit`, not here.
    """
    report = AssemblyReport()
    if measurements is None or len(measurements.part_bboxes) < 2:
        return report

    report.order = measurements.order
    report.unchecked.extend(measurements.unchecked)
    _check_fit(measurements, spec, report)
    _check_overlap(measurements, report)
    _check_mating(measurements, spec, report)
    _check_order(measurements, report)
    return report


def mating_tolerance(clearance_mm: float) -> float:
    """How close two parts must come to count as joined, in millimetres."""
    return max(clearance_mm * MATING_TOLERANCE_FACTOR, MATING_TOLERANCE_FLOOR_MM)


def _check_fit(m: AssemblyMeasurements, spec: AssemblySpec, report: AssemblyReport) -> None:
    volume = spec.volume
    for index, box in enumerate(m.part_bboxes):
        label = _label(index)
        size = _dimensions(box)
        if size is None:
            # ``too_big_for`` answers "" for a box it cannot read, and every caller
            # there treats that as silence deliberately. Reading it as "fits" here
            # would present an unchecked part as a checked one, so the guard sits
            # in front of the call rather than inside it.
            report.unchecked.append(
                f"build volume fit for {label}: its bounding box is not three finite numbers"
            )
            continue
        if too_big_for(box, volume):
            report.failures.append(
                f"{label} is {_size(size)} mm and does not fit the printer's "
                f"{_size((volume.width, volume.depth, volume.height))} mm build volume; "
                "split the model into more parts, or move the seam"
            )


def _check_overlap(m: AssemblyMeasurements, report: AssemblyReport) -> None:
    for entry in m.overlaps:
        pair = _pair(entry)
        if pair is None:
            report.unchecked.append(f"overlap between two parts: unreadable entry {entry!r}")
            continue
        first, second, shared = pair
        if shared > OVERLAP_EPSILON_MM3:
            report.failures.append(
                f"{_label(first)} and {_label(second)} share {shared:g} mm^3 of interior volume; "
                "two parts cannot occupy the same space, so widen the clearance on that joint"
            )


def _check_mating(m: AssemblyMeasurements, spec: AssemblySpec, report: AssemblyReport) -> None:
    count = len(m.part_bboxes)
    tolerance = mating_tolerance(spec.clearance_mm)
    neighbours: dict[int, set[int]] = {index: set() for index in range(count)}
    for entry in m.gaps:
        pair = _pair(entry)
        if pair is None:
            report.unchecked.append(f"gap between two parts: unreadable entry {entry!r}")
            continue
        first, second, distance = pair
        if not (0 <= first < count and 0 <= second < count):
            continue
        if distance <= tolerance:
            neighbours[first].add(second)
            neighbours[second].add(first)

    groups = _groups(neighbours, count)
    if len(groups) == 1:
        return
    alone = sorted(next(iter(group)) for group in groups if len(group) == 1)
    if alone:
        names = ", ".join(_label(index) for index in alone)
        report.failures.append(
            f"{names} comes no closer than {_size1(tolerance)} mm to any other part; "
            "a part that mates with nothing is not joined to the assembly"
        )
    if len(groups) - len(alone) > 1:
        report.failures.append(
            f"the parts fall into {len(groups)} separate groups that do not meet each other, "
            "so they do not reassemble into one shape"
        )


def _check_order(m: AssemblyMeasurements, report: AssemblyReport) -> None:
    if m.order is not None:
        return
    if m.trapped:
        names = ", ".join(_label(index) for index in sorted(m.trapped))
        report.failures.append(
            f"no assembly order exists: {names} cannot be brought into place without passing "
            "through another part; reorient the joint so it slides in along one axis"
        )
        return
    report.failures.append(
        "no assembly order exists: the parts cannot be brought together one at a time "
        "without collision"
    )


def _groups(neighbours: dict[int, set[int]], count: int) -> list[set[int]]:
    """The connected components of the mating graph."""
    seen: set[int] = set()
    groups: list[set[int]] = []
    for start in range(count):
        if start in seen:
            continue
        group: set[int] = set()
        pending = [start]
        while pending:
            index = pending.pop()
            if index in group:
                continue
            group.add(index)
            pending.extend(neighbours[index] - group)
        seen |= group
        groups.append(group)
    return groups


def _pair(entry: object) -> tuple[int, int, float] | None:
    """An ``[i, j, value]`` entry as usable numbers, or ``None``."""
    try:
        first, second, value = entry  # type: ignore[misc]
        return int(first), int(second), float(value)
    except (TypeError, ValueError):
        return None


def _label(index: int) -> str:
    """Parts are counted from one in anything a person or the model reads."""
    return f"part {index + 1}"


def _size(size: tuple[float, float, float]) -> str:
    return " x ".join(_size1(value) for value in size)


def _size1(value: float) -> str:
    """A measurement for a sentence, rounded past the geometry's own noise."""
    return f"{round(value, 1):g}"


# --- measurement ----------------------------------------------------------
#
# Everything below runs where the solids are live, inside the worker child.
# ``build123d`` is imported inside the functions rather than at module scope, so
# importing this module for its policy half costs nothing and pulls in no kernel.

#: The six axis directions every part is tried along first.
#:
#: The model is asked to sweep each joint profile along an axis lying in the build
#: plane, so a correct interlock comes apart along one of these. They are not
#: relied on alone -- :func:`_candidate_directions` adds directions derived from
#: the geometry, because a candidate that turns out to be blocked costs one probe
#: while a missing one costs a false refusal.
_AXES = (
    (1.0, 0.0, 0.0),
    (-1.0, 0.0, 0.0),
    (0.0, 1.0, 0.0),
    (0.0, -1.0, 0.0),
    (0.0, 0.0, 1.0),
    (0.0, 0.0, -1.0),
)

#: How many solid-to-solid collision probes the order search may spend.
#:
#: The executor's wall clock covers the build, this measurement and the export
#: together, and a blown clock returns no summary at all rather than a partial
#: one -- so the search needs a bound of its own rather than borrowing that one.
#: Sized from the measured cost of a probe. It bounds the *number* of kernel
#: calls and nothing else -- ``MEASUREMENT_TIME_BUDGET_SECONDS`` beside it is
#: what bounds their duration. Running out is reported as an unestablished
#: check, never as a pass: work that stopped early has shown nothing.
#:
#: One budget covers the whole measurement, the pairwise comparisons included.
#: Bounding only the order search left the O(n^2) phase ahead of it free to
#: spend the executor's entire wall clock on a model with very many parts.
ORDER_PROBE_BUDGET = 20_000

#: How long the whole measurement may spend, in seconds.
#:
#: A second bound beside the probe count, because the two fail differently.
#: The count stops a model that produced very many parts; the clock stops a
#: single part whose topology makes one boolean operation take seconds.
#: Counting alone rests on probes costing roughly the same, which a
#: pathological solid disproves -- and the executor returns no summary at all
#: when its own wall clock runs out, so this has to stop first to say anything.
MEASUREMENT_TIME_BUDGET_SECONDS = 10.0

#: Fraction of the thinnest part dimension used as the travel step.
#:
#: A step larger than a part can step straight over it: the moved solid is clear
#: on both sides of a thin wall and the collision is never seen, which reports a
#: walled-in part as free. Halving the thinnest dimension puts at least one
#: sample inside anything that could block.
_STEP_FRACTION = 0.5


def measure_assembly(
    parts,
    probe_budget: int = ORDER_PROBE_BUDGET,
    time_budget: float = MEASUREMENT_TIME_BUDGET_SECONDS,
) -> AssemblyMeasurements:
    """Measure the relations between ``parts``, which must already be in millimetres.

    The scaled solids, not the authoring-unit ones: the build volume this is
    checked against is in millimetres, and measuring the unscaled result would
    silently compare the two.
    """
    boxes = [_extent(part) for part in parts]
    unchecked: list[str] = []
    overlaps: list[list[float]] = []
    gaps: list[list[float]] = []
    budget = _Budget(probe_budget, time_budget)

    for first in range(len(parts)):
        for second in range(first + 1, len(parts)):
            try:
                budget.spend()
            except _BudgetSpent:
                unchecked.append(
                    "overlap and gap between every pair: the measurement ran out of "
                    f"budget after {len(gaps)} of "
                    f"{len(parts) * (len(parts) - 1) // 2} pairs"
                )
                return AssemblyMeasurements(
                    part_bboxes=boxes,
                    overlaps=overlaps,
                    gaps=gaps,
                    order=None,
                    trapped=[],
                    unchecked=unchecked,
                )
            shared = _shared_volume(parts[first], parts[second])
            if shared is None:
                unchecked.append(
                    f"shared volume between {_label(first)} and {_label(second)}: "
                    "the intersection could not be computed"
                )
            elif shared > 0.0:
                overlaps.append([first, second, shared])

            distance = _distance(parts[first], parts[second])
            if distance is None:
                unchecked.append(
                    f"gap between {_label(first)} and {_label(second)}: "
                    "the distance could not be computed"
                )
            else:
                gaps.append([first, second, distance])

    order, trapped, order_unchecked = _disassembly_order(parts, budget)
    unchecked.extend(order_unchecked)
    return AssemblyMeasurements(
        part_bboxes=boxes,
        overlaps=overlaps,
        gaps=gaps,
        order=order,
        trapped=trapped,
        unchecked=unchecked,
    )


def _extent(part) -> list[float]:
    size = part.bounding_box().size
    return [float(size.X), float(size.Y), float(size.Z)]


def _shared_volume(first, second) -> float | None:
    """The interior volume two parts share; ``0.0`` when they only touch.

    ``intersect`` rather than ``&``, which raises on an empty result where this
    answers ``None``.

    What excludes a touching pair is summing the *solids* of the intersection:
    two parts meeting at a face intersect in a face, which contributes no solid
    and so no volume. ``include_touched=False`` says the same thing to the kernel
    and is kept for that, but the sum is what the answer rests on -- flipping the
    flag alone does not change it.
    """
    try:
        shared = first.intersect(second, include_touched=False)
    except Exception:  # noqa: BLE001 - an unrunnable probe is reported, never assumed away
        return None
    if shared is None:
        return 0.0
    try:
        return float(sum(solid.volume for solid in shared.solids()))
    except Exception:  # noqa: BLE001 - same: report rather than guess
        return None


def _distance(first, second) -> float | None:
    try:
        return float(first.distance_to(second))
    except Exception:  # noqa: BLE001 - same: report rather than guess
        return None


def _disassembly_order(parts, budget):
    """An order in which the parts can be assembled, or why there is none.

    Found by taking the assembly apart: repeatedly free a part that can be
    translated clear of the rest, then reverse what that produced. An order found
    this way is one that works, because every move was checked against the solids
    rather than inferred. The converse does not hold -- a split that only comes
    apart along a curve, or by moving two parts at once, is refused here. That
    costs a repair attempt, and it is the price of an answer that is never wrong
    when it says yes.
    """
    if len(parts) < 2:
        return list(range(len(parts))), [], []

    spheres = [_sphere(part) for part in parts]
    step = _step(parts)
    if step is None:
        return None, [], ["assembly order: a part has no measurable size"]

    remaining = list(range(len(parts)))
    removed: list[int] = []
    while remaining:
        freed = None
        for index in remaining:
            others = [other for other in remaining if other != index]
            try:
                if _can_be_freed(parts, spheres, index, others, step, budget):
                    freed = index
                    break
            except _BudgetSpent:
                return (
                    None,
                    [],
                    ["assembly order: the search ran out of collision probes before it could rule"],
                )
        if freed is None:
            return None, sorted(remaining), []
        remaining.remove(freed)
        removed.append(freed)
    # Removing in this order works, so assembling in the reverse of it does.
    return list(reversed(removed)), [], []


def _can_be_freed(parts, spheres, index, others, step, budget) -> bool:
    for direction in _candidate_directions(spheres, index, others):
        if not _blocked_along(parts, spheres, index, others, direction, step, budget):
            return True
    return False


def _blocked_along(parts, spheres, index, others, direction, step, budget) -> bool:
    part = parts[index]
    centre, radius = spheres[index]
    for other in others:
        other_centre, other_radius = spheres[other]
        window = _travel_window(centre, radius, other_centre, other_radius, direction)
        if window is None:
            # Their bounding spheres cannot meet along this direction, so the
            # solids cannot either. Skipping is exact, not an approximation.
            continue
        start, stop = window
        position = start
        while position <= stop:
            budget.spend()
            if _meets(part, parts[other], direction, position):
                return True
            position += step
    return False


def _travel_window(centre, radius, other_centre, other_radius, direction):
    """The distances along ``direction`` where two bounding spheres could meet.

    ``direction`` must be a unit vector; every caller passes one, and a longer one
    would scale ``along`` and quietly falsify the arithmetic below rather than
    fail.

    Moving one centre by ``t`` along the heading, the two spheres overlap while
    ``|heading * t - delta| <= reach``. Squaring gives
    ``t^2 - 2t(delta . heading) + |delta|^2 - reach^2 <= 0``, and since
    ``perpendicular^2 = |delta|^2 - along^2`` its roots are exactly
    ``along +/- sqrt(reach^2 - perpendicular^2)``. So the returned pair is the
    overlap interval itself, not an estimate of it: no real root means the two can
    never meet along this heading, and an interval wholly behind the start means
    moving forward never reaches it.

    Skipping on that answer is exact rather than an approximation, because a
    bounding sphere contains its solid -- spheres that miss guarantee solids that
    miss. The converse does not hold, which is why a window that exists is then
    stepped through against the solids rather than believed.
    """
    from build123d import Vector

    delta = Vector(*other_centre) - Vector(*centre)
    heading = Vector(*direction)
    along = delta.dot(heading)
    reach = radius + other_radius
    perpendicular = (delta - heading * along).length
    if perpendicular > reach:
        return None
    half = (reach**2 - perpendicular**2) ** 0.5
    stop = along + half
    if stop <= 0.0:
        return None
    return max(0.0, along - half), stop


def _meets(part, other, direction, distance) -> bool:
    from build123d import Location, Vector

    moved = part.moved(Location(Vector(*direction) * distance))
    shared = _shared_volume(moved, other)
    # An unrunnable probe must not read as "clear" -- that would free a part the
    # search could not actually check, and hand back an order that does not work.
    return shared is None or shared > OVERLAP_EPSILON_MM3


def _candidate_directions(spheres, index, others):
    """The six axes, plus a direction leading away from each remaining part.

    The geometry-derived ones cover a joint whose slide axis is not aligned to an
    axis. Adding a candidate can only remove a false refusal, never create a false
    pass: every direction offered is still checked against the solids, so one that
    is not really free is rejected on the probe.
    """
    from build123d import Vector

    directions = [Vector(*axis) for axis in _AXES]
    centre = Vector(*spheres[index][0])
    for other in others:
        away = centre - Vector(*spheres[other][0])
        if away.length > 1e-9:
            directions.append(away.normalized())
    return directions


def _sphere(part):
    """A part's bounding-sphere centre and radius, for the cheap pruning test."""
    box = part.bounding_box()
    centre = box.center()
    size = box.size
    radius = 0.5 * (size.X**2 + size.Y**2 + size.Z**2) ** 0.5
    return (float(centre.X), float(centre.Y), float(centre.Z)), float(radius)


def _step(parts) -> float | None:
    thinnest = None
    for part in parts:
        size = part.bounding_box().size
        smallest = min(float(size.X), float(size.Y), float(size.Z))
        if thinnest is None or smallest < thinnest:
            thinnest = smallest
    if thinnest is None or thinnest <= 0.0:
        return None
    return thinnest * _STEP_FRACTION


class _BudgetSpent(Exception):
    """Raised when the order search has used every probe it was allowed."""


class _Budget:
    """How much kernel work the measurement may still do.

    Bounded two ways at once; see the two module constants for why one is not
    enough. Checked before each probe rather than after, so an exhausted budget
    costs nothing further.
    """

    def __init__(self, probes: int, seconds: float) -> None:
        self._left = probes
        self._deadline = time.monotonic() + seconds

    def spend(self) -> None:
        if self._left <= 0 or time.monotonic() >= self._deadline:
            raise _BudgetSpent
        self._left -= 1
