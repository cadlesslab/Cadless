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
