"""Geometric post-conditions for a multi-part build: does it actually go together?

A turn that asks for an assembly gets its parts from the model, which is the only
participant that knows where a seam does least harm -- and which has no way to
guarantee the result fits, mates, or can be put together. This module is where
that becomes something the engine establishes rather than something the model
claims. The checks, each able to name itself when it fails:

  * **build volume fit** -- every part printable on its own;
  * **no shared interior volume** -- two parts cannot occupy the same space;
  * **mating** -- the parts form one connected assembly rather than loose pieces;
  * **assembly order** -- an order exists in which each part reaches its place;
  * **requested size** -- the whole build is the size the request gave, where it
    gave one, because a model shrunk until its parts fit passes every check of
    the parts and is still not what was asked for.

The split of work mirrors :mod:`cadless.assertions`: an
:class:`AssemblyMeasurements` carries numbers measured by
:mod:`cadless.assembly_measure`, and :func:`evaluate_assembly` is a pure function --
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

Which checks a build faces
--------------------------
A turn that asked for an assembly faces every check, against the printer it is
for. Any other build of several parts faces :data:`PARTS_CHECKS` alone: whether
two parts share volume, which is wrong in any model. Fitting the bed is
:mod:`cadless.print_fit`'s question for a build nobody asked to split, as it is
for a lone part nobody asked to split -- a lone part on a turn that did ask is
held to the bed here, by :func:`evaluate_lone_part`; parts that touch nothing
are not wrong in a model nobody asked to join -- an exploded view, a set of
separate objects; and a part that cannot come out is not wrong in one printed in
place -- a chain, a ball in a cage. The requested size is held to by
:func:`evaluate_requested_size` on a turn that asked and gave one, whatever the
build came back as: one part, a measured split, or parts nothing could measure.
"""

from __future__ import annotations

import itertools
import math
import reprlib
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field

from cadless.assembly_tree import readable, unnamed
from cadless.joint_sizes import REACH_MOST_MM, pieces_along
from cadless.print_fit import _dimensions, _number, too_big_for
from cadless.printer_profile import (
    PRINTER_PROFILE_LIMITS,
    AssemblySpec,
    BuildVolume,
    requested_lengths,
    requested_phrase,
)

#: How far apart two parts may sit and still count as mating, as a multiple of the
#: joint clearance.
#:
#: A correct joint's closest approach can be the clearance itself: every fit but a
#: seam the split helper makes cuts its socket that much larger than the tab it
#: receives, so those faces never touch. A threshold at or below the clearance
#: would therefore refuse every such split. The multiple leaves room for a chamfer
#: or fillet on the mating faces, where the nearest points of two joined parts sit
#: further apart than the nominal gap.
MATING_TOLERANCE_FACTOR = 4.0

#: The floor under that tolerance, in millimetres, so a printer configured with no
#: clearance at all still leaves a usable window rather than demanding contact.
MATING_TOLERANCE_FLOOR_MM = 1.0

#: The most that tolerance comes to: what the largest joint clearance a printer
#: profile holds gives.
MATING_TOLERANCE_MOST_MM = (
    PRINTER_PROFILE_LIMITS["printer_joint_clearance"][1] * MATING_TOLERANCE_FACTOR
)

#: Below this shared volume a boolean intersection is arithmetic noise rather than
#: interpenetration. Faces that merely touch intersect at exactly zero, and the
#: distinction between touching and overlapping is the line this module draws.
OVERLAP_EPSILON_MM3 = 1e-9

#: Below this shared volume the overlap reads as a part grazing the other, or a
#: socket that does not clear its tab: narrower than the tab, or off its place.
#: At or above it, as a part set into the other, or a tab sitting in solid
#: material because the socket was cut outside the receiving part, on the wrong
#: face, or not at all. The part set into another is named first because that
#: is what the overlaps of generated assemblies turned out to be -- a wheel laid
#: into the chassis, an axle with no hole on its line -- where a sentence about
#: tabs alone sent the repair to the joints. The line is drawn to separate the
#: clearance-sized shared volumes from the tab-sized ones; a socket shrunk by
#: the clearance all round lands above it on all but the smallest tabs, and
#: then only the stated reason is off, since both sentences end with the same
#: fix. It chooses the sentence the model reads, never the verdict: any shared
#: volume above the epsilon refuses.
TAB_IN_SOLID_MM3 = 50.0

#: What either sentence ends with: how a tab is cleared -- by the split helper for
#: a seam, since a seam it makes cannot hold a tab in solid material, and by hand
#: for any other fit.
_SOCKET_FIX = (
    "re-make a seam with split_and_join along the axis its part goes in on, which "
    "cuts the socket itself; for any other fit, subtract the socket, the tab's shape "
    "enlarged by the clearance on every side, from the receiving part at the place "
    "the tab occupies when assembled"
)

#: What both size refusals say of a seam's joint. A split sized by the cut alone
#: put the seam in the middle and got back a half too long by the joint's reach,
#: so the refusal says where that length goes and that the half must fit with it.
_JOINT_ROOM = (
    "the half behind a seam's plane carries its tabs or pegs, which stand up to "
    f"{REACH_MOST_MM:g} mm past the cut, so that half must fit the bed with them"
)

#: How far a side of the whole build may stand from the size the request gave,
#: in millimetres, at the least: enough to absorb the kernel's rounding.
REQUESTED_SIZE_SLACK_MM = 1.0

#: The same slack as a fraction of the side, used where it is the larger: a long
#: side, where the cuts and the clearance they leave fall, gets room in
#: proportion. A model resized to dodge the bed moves a side by far more than
#: either, so the slack decides nothing but noise.
REQUESTED_SIZE_SLACK_FRACTION = 0.01

#: Prefix on every reason the order search could not rule.
#:
#: A constant rather than a repeated literal because two places depend on it
#: agreeing: the search writes it, and the policy reads it to tell 'the search
#: could not rule' from 'it ruled and found nothing'.
_ORDER_UNCHECKED_PREFIX = "assembly order:"
# How what could not be established about a pair is worded -- by the measurement,
# and by the checks reading its tables -- so that a report can tell which check
# each such line bears on.
_PAIRS_UNCHECKED_PREFIX = "overlap and gap between every pair:"
_SHARED_UNCHECKED_PREFIX = "shared volume between"
_GAP_UNCHECKED_PREFIX = "gap between"


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

    ``releases`` carries, per part index, the unit direction the search actually
    took that part out along -- the heading it proved clear, not one derived
    afterwards from the shape. It is empty overall when no order was found, and
    the entry for the part left standing last is empty too: nothing remains to
    block that one, so any heading would pass and none would be a measurement.

    ``overlap_boxes`` carries, for a pair in ``overlaps``, the box around the
    volume the two share, ``[i, j, x0, y0, z0, x1, y1, z1]`` in the script's own
    coordinates, so a refusal can say where to look. ``freed_without_overlaps``
    is whether an order is found once each overlapping pair is let pass through
    the other, after a search that trapped parts while parts overlap; ``None``
    wherever no such second search ruled -- it was never needed or never
    reached, or it could not finish. Both are kept apart from the tables a
    verdict is read from, so a reader of another release loses a sentence's
    detail, never a verdict.
    """

    part_bboxes: list[list[float]] = field(default_factory=list)
    overlaps: list[list[float]] = field(default_factory=list)
    gaps: list[list[float]] = field(default_factory=list)
    order: list[int] | None = None
    trapped: list[int] = field(default_factory=list)
    unchecked: list[str] = field(default_factory=list)
    releases: list[list[float]] = field(default_factory=list)
    overlap_boxes: list[list[float]] = field(default_factory=list)
    freed_without_overlaps: bool | None = None

    @classmethod
    def from_payload(cls, data: object) -> AssemblyMeasurements | None:
        """Rebuild from a worker payload, or ``None`` when there is nothing to read.

        Key by key rather than ``cls(**data)``: an api and a worker running
        different engine builds disagree about the field set, and unpacking turns
        that skew into a ``TypeError`` that fails the whole call. Read this way an
        older reader ignores a field it does not know and a newer one falls back
        to the defaults, which degrades instead of breaking.

        A table that is there but is no list is read as empty and said to be,
        where read as it came a number raised and text was read a letter to an
        entry. It is said in the words of the check it measured for, so a check
        that is not faced is not refused over it; a table no check judges -- the
        headings, where overlaps sit -- is dropped unsaid.
        """
        if not isinstance(data, dict):
            return None
        tables = {key: _listed(data.get(key)) for key in _TABLES}
        unchecked = tables["unchecked"] or []
        unchecked += [
            f"{what}: the measurement sent {_quoted(data[key])} rather than a list"
            for key, what in _TABLES.items()
            if tables[key] is None and what is not None
        ]
        return cls(
            part_bboxes=tables["part_bboxes"] or [],
            overlaps=tables["overlaps"] or [],
            gaps=tables["gaps"] or [],
            order=_index_list(data.get("order")),
            trapped=_index_list(data.get("trapped")) or [],
            unchecked=unchecked,
            releases=tables["releases"] or [],
            overlap_boxes=tables["overlap_boxes"] or [],
            freed_without_overlaps=_ruling(data.get("freed_without_overlaps")),
        )


@dataclass
class AssemblyReport:
    """Outcome of checking one build's parts against the printer they are for."""

    failures: list[str] = field(default_factory=list)
    unchecked: list[str] = field(default_factory=list)
    order: list[int] | None = None
    #: Which parts were found joined to which: one sorted neighbour list per part,
    #: in part order. ``None`` where mating was never checked -- fewer than two
    #: parts, or nothing measured -- which is not the same as a build whose parts
    #: turned out to touch nothing, and that one carries an empty list per part.
    #:
    #: A list rather than a dict keyed by part index, because this travels on to
    #: the model as JSON and ``json.dumps`` turns integer keys into strings
    #: without saying so, so a round trip would hand back a different type.
    joints: list[list[int]] | None = None

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


#: Every check of the parts, for a turn that asked for an assembly.
ASSEMBLY_CHECKS = frozenset({"fit", "overlap", "mating", "order"})

#: The checks any other build of several parts faces (see the module docstring).
PARTS_CHECKS = frozenset({"overlap"})

#: The name the requested-size check goes by in a verdict. In neither set above:
#: it judges the whole build rather than its parts, through
#: :func:`evaluate_requested_size`, so :func:`evaluate_assembly` never runs it.
SIZE_CHECK = "size"


def _concerns(reason) -> str | None:
    """The check an unestablished measurement bears on, or ``None`` where it says not.

    A reason that is not text says nothing about its check, so it counts against
    every report rather than raising out of a function that must not.
    """
    if not isinstance(reason, str):
        return None
    if reason.startswith(_ORDER_UNCHECKED_PREFIX):
        return "order"
    if reason.startswith(_GAP_UNCHECKED_PREFIX):
        return "mating"
    if reason.startswith((_SHARED_UNCHECKED_PREFIX, _PAIRS_UNCHECKED_PREFIX)):
        return "overlap"
    return None


def evaluate_assembly(
    measurements: AssemblyMeasurements | None,
    spec: AssemblySpec,
    *,
    checks: frozenset[str] = ASSEMBLY_CHECKS,
    names: Sequence[str | None] | None = None,
) -> AssemblyReport:
    """Check ``measurements`` against ``spec``; pure, and never raises.

    A build with nothing measured, or with fewer than two parts, has no relation
    to check and yields an empty passing report -- judging a lone part against the
    build volume belongs to :func:`evaluate_lone_part` on a turn that asked, and
    to :mod:`cadless.print_fit` otherwise.

    ``checks`` names which checks can fail the report. A measurement that could
    not be taken counts against a check named here, or against every report
    where it does not say which check it bears on; a name that is no check is
    reported rather than ignored, so a misspelling cannot pass a build unjudged.
    The joints are described whichever are named: a guide to parts that came
    apart still says which ones touch.

    ``names`` are the parts' names in part order, as their build gave them; they
    change what the sentences call a part and nothing that is judged.
    """
    report = AssemblyReport()
    for name in sorted(checks - ASSEMBLY_CHECKS):
        report.unchecked.append(f"no check is named {name!r}, so nothing was judged under it")
    if measurements is None:
        return report
    # Carried before the part-count gate below: a payload that measured
    # nothing still has something to say, and dropping it here turned an
    # executor that failed outright into a silent pass.
    report.unchecked.extend(
        reason if isinstance(reason, str) else f"a reason that is not text: {_quoted(reason)}"
        for reason in measurements.unchecked
        if _concerns(reason) is None or _concerns(reason) in checks
    )
    if len(measurements.part_bboxes) < 2:
        return report

    labels = part_labels(len(measurements.part_bboxes), names)
    report.order = measurements.order
    if not _is_permutation(report.order, len(measurements.part_bboxes)):
        # Withheld whatever is judged: an order of other parts would be played
        # and described as this build's. Why it was withheld is the order
        # check's to say, below, where that check is faced.
        report.order = None
    if "fit" in checks:
        _check_fit(measurements, spec, report, labels)
    if "overlap" in checks:
        _check_overlap(measurements, report, labels, joined="mating" in checks)
    _check_mating(measurements, spec, report, labels, judge="mating" in checks)
    if "order" in checks:
        _check_order(measurements, report, labels)
    return report


def mating_tolerance(clearance_mm: float) -> float:
    """How close two parts must come to count as joined, in millimetres.

    Never more than ``MATING_TOLERANCE_MOST_MM``, whatever clearance a spec built
    by hand carries.
    """
    return min(
        max(clearance_mm * MATING_TOLERANCE_FACTOR, MATING_TOLERANCE_FLOOR_MM),
        MATING_TOLERANCE_MOST_MM,
    )


def _length_scale(value: object) -> float | None:
    """``value`` where it can bring a length to millimetres, else ``None``.

    Only a positive finite number can. Multiplied through, NaN or an infinity
    left a box no check could read, which the fit passes in silence, and zero or
    a negative scale a size nothing measured: a part five times the bed fitted.
    """
    scale = _number(value)
    return scale if scale is not None and math.isfinite(scale) and scale > 0.0 else None


def _millimetres(bbox: Sequence[float] | None, scale: float) -> tuple[float, float, float] | None:
    """``bbox`` brought to millimetres by ``scale``, or ``None`` where it is no box there.

    Read again after scaling: a side too long for a float once multiplied is no
    more a length than one that was never there.
    """
    size = _dimensions(bbox)
    return None if size is None else _dimensions(tuple(extent * scale for extent in size))


def evaluate_lone_part(
    bbox: Sequence[float] | None, spec: AssemblySpec, *, export_scale: float = 1.0
) -> AssemblyReport:
    """Check a build that came back as one solid against the printer it is for.

    A one-part build has no joints, overlaps or order to check, so this is the fit
    alone. It is held to on a turn that asked for an assembly because the model
    giving up on the split and returning the whole thing is the one answer the
    per-part checks never see, and it was being kept as the assembly it is not.
    The refusal is worded as a part's, cuts included, so the repair that follows
    is told the same thing a refused part tells it. ``bbox`` is the worker's, in
    authoring units, and ``export_scale`` brings it to the millimetres the part
    boxes and the bed are in; a box that cannot be read is reported rather than
    passed, before any scaling, as the per-part fit does.
    """
    report = AssemblyReport()
    scale = _length_scale(export_scale)
    if scale is None:
        report.unchecked.append(
            f"build volume fit for the one part: the scale to millimetres, {export_scale!r}, "
            "is not a positive finite number"
        )
        return report
    size = _millimetres(bbox, scale)
    if size is None:
        report.unchecked.append(
            "build volume fit for the one part: its bounding box is not three finite lengths"
        )
        return report
    volume = spec.volume
    if too_big_for(size, volume):
        report.failures.append(
            f"the build is one part, {_size(size)} mm, and does not fit the printer's "
            f"{_size((volume.width, volume.depth, volume.height))} mm build volume; "
            f"cut it into {_cuts(size, volume)}, joined as the brief asks -- "
            f"{_JOINT_ROOM} -- and keep the model's size: do not shrink it"
        )
    return report


def evaluate_requested_size(
    bbox: Sequence[float] | None, spec: AssemblySpec, *, export_scale: float = 1.0
) -> AssemblyReport:
    """Check the whole build against the overall sides the request gave.

    Nothing where ``spec`` carries no requested size. Otherwise ``bbox`` -- the
    worker's box around every part as placed, in authoring units, brought to
    millimetres by ``export_scale`` -- must have a side within the slack of each
    requested length, a different side for each, so which way the model lies on
    the bed is left to the split and a side the request left open holds nothing.
    One sentence names the build's size and what was asked for when its sides
    cannot keep every requested length, a different side for each; a box or a
    requested size that cannot be read is
    reported as unchecked rather than passed, as the fit is.
    """
    report = AssemblyReport()
    if spec.requested_mm is None:
        return report
    requested = requested_lengths(spec.requested_mm)
    if requested is None:
        report.unchecked.append(
            "overall size against the request: the requested size is not one to "
            "three positive finite lengths"
        )
        return report
    scale = _length_scale(export_scale)
    if scale is None:
        report.unchecked.append(
            f"overall size against the request: the scale to millimetres, {export_scale!r}, "
            "is not a positive finite number"
        )
        return report
    size = _millimetres(bbox, scale)
    if size is None:
        report.unchecked.append(
            "overall size against the request: the build's bounding box is not three finite lengths"
        )
        return report
    if not any(
        all(
            abs(have - want) <= requested_slack(want)
            for have, want in zip(sides, requested, strict=True)
        )
        for sides in itertools.permutations(size, len(requested))
    ):
        report.failures.append(
            f"the whole build is {_size(size)} mm, but the request asked for "
            f"{requested_phrase(requested)}: keep every dimension the request gives and meet "
            "the bed by splitting the model into parts that each fit it, never by "
            "shrinking or enlarging the model"
        )
    return report


def requested_slack(length: float) -> float:
    """How far a side may stand from a requested ``length`` and still keep it, in mm."""
    return max(REQUESTED_SIZE_SLACK_MM, length * REQUESTED_SIZE_SLACK_FRACTION)


def _check_fit(
    m: AssemblyMeasurements,
    spec: AssemblySpec,
    report: AssemblyReport,
    labels: Sequence[str],
) -> None:
    volume = spec.volume
    for index, box in enumerate(m.part_bboxes):
        label = _at(labels, index)
        size = _dimensions(box)
        if size is None:
            # ``too_big_for`` answers "" for a box it cannot read, and every caller
            # there treats that as silence deliberately. Reading it as "fits" here
            # would present an unchecked part as a checked one, so the guard sits
            # in front of the call rather than inside it.
            report.unchecked.append(
                f"build volume fit for {label}: its bounding box is not three finite lengths"
            )
            continue
        if too_big_for(box, volume):
            report.failures.append(
                f"{label} is {_size(size)} mm and does not fit the printer's "
                f"{_size((volume.width, volume.depth, volume.height))} mm build volume; "
                f"cut it into {_cuts(size, volume)}, or move its seams so each piece "
                f"fits -- {_JOINT_ROOM} -- and keep the model's size: do not shrink it"
            )


def _cuts(size: tuple[float, float, float], volume: BuildVolume) -> str:
    """How many pieces each exceeded axis needs, as the model reads it.

    Said per axis because "split the model into more parts" was answered with
    another cut along the axis already cut, or with a smaller model: a count on
    each axis the part exceeds is an instruction that can be followed only one
    way. Counted in both footprint orientations, because a part may be placed on
    the bed turned a quarter and the fit it is held to allows that; the
    orientation needing fewer pieces is the one said, and the turn is named when
    it is the turned one, so the count asks for no cut the fit would not.
    """
    as_is = _pieces(size, (volume.width, volume.depth, volume.height))
    turned = _pieces(size, (volume.depth, volume.width, volume.height))
    if math.prod(turned) < math.prod(as_is):
        counts, note = turned, ", turned a quarter on the bed"
    else:
        counts, note = as_is, ""
    cuts = " and ".join(
        f"{count} along its {axis}"
        for axis, count in zip(("width", "depth", "height"), counts, strict=True)
        if count > 1
    )
    return cuts + note


def _pieces(
    size: tuple[float, float, float], limits: tuple[float, float, float]
) -> tuple[int, int, int]:
    """Pieces per axis that straight cuts leave: :func:`pieces_along` for each extent and limit."""
    width, depth, height = (
        pieces_along(extent, limit) for extent, limit in zip(size, limits, strict=True)
    )
    return width, depth, height


def _read_overlaps(
    m: AssemblyMeasurements, labels: Sequence[str]
) -> tuple[list[tuple[int, int, float]], list[str]]:
    """The overlap entries a check can rule on, and why each of the others cannot be.

    One reading with two readers: the overlap check refuses what it reads, and
    the order check believes "while parts share volume" only beside it.
    Read twice, the two could disagree about which entries count, and the order
    sentence would then blame an overlap the overlap check itself could not read.
    """
    readable: list[tuple[int, int, float]] = []
    problems: list[str] = []
    for entry in m.overlaps:
        pair = _pair(entry)
        if pair is None:
            problems.append(f"overlap between two parts: unreadable entry {_quoted(entry)}")
            continue
        first, second, shared = pair
        if not (0 <= first < len(m.part_bboxes) and 0 <= second < len(m.part_bboxes)):
            problems.append(f"overlap entry {_quoted(entry)} names a part that does not exist")
            continue
        if first == second:
            # Unlike a gap entry, one that names no pair cannot be passed over:
            # nothing checks this table for the pairs it leaves out, so it may be
            # what is left of one that did.
            problems.append(f"overlap entry {_quoted(entry)} names one part twice")
            continue
        if _measurement(shared) is None:
            problems.append(
                f"{_SHARED_UNCHECKED_PREFIX} {_at(labels, first)} and {_at(labels, second)}: "
                f"{shared!r} is not a volume that can be measured"
            )
            continue
        readable.append((first, second, shared))
    return readable, problems


def _check_overlap(
    m: AssemblyMeasurements, report: AssemblyReport, labels: Sequence[str], *, joined: bool
) -> None:
    places = _overlap_places(m.overlap_boxes)
    readable, problems = _read_overlaps(m, labels)
    report.unchecked.extend(problems)
    for first, second, shared in readable:
        if shared > OVERLAP_EPSILON_MM3:
            # A build nobody asked to join was never told of seams, tabs or a
            # clearance, so its sentence names none of them.
            if not joined:
                cause = "cut the shared volume out of one of them so they only touch"
            elif shared < TAB_IN_SOLID_MM3:
                cause = (
                    "a sliver that size is a part grazing the other, or a socket narrower "
                    "than its tab or off its place: move the part clear of the other there, "
                    f"or {_SOCKET_FIX}"
                )
            else:
                cause = (
                    "that much is a part set into the other, or a tab sitting in solid "
                    "material because its socket was cut outside the receiving part, on the "
                    "wrong face, or not at all: move the part out of the other there, or, "
                    f"where it is meant to pass into the other, {_SOCKET_FIX}"
                )
            where = places.get((min(first, second), max(first, second)), "")
            report.failures.append(
                f"{_at(labels, first)} and {_at(labels, second)} share "
                f"{shared:g} mm^3 of interior volume{where}; two parts cannot occupy the "
                f"same space; {cause}"
            )


def mating_graph(
    gaps: list[list[float]], part_count: int, tolerance: float
) -> tuple[dict[int, set[int]], list[str]]:
    """Which parts sit close enough to count as joined, and what could not be read.

    One derivation with two readers. Connectivity is ruled from this graph, and so
    is anything that goes on to describe the joints; deriving it twice would let a
    description name a joint the same build was refused for not having.

    The unreadable entries come back rather than being judged here, because what
    an unreadable gap means -- refuse, or pass over -- is the caller's policy and
    not a property of reading the table.
    """
    neighbours, problems, _ = _read_gaps(gaps, part_count, tolerance)
    return neighbours, problems


def _read_gaps(
    gaps: list[list[float]],
    part_count: int,
    tolerance: float,
    labels: Sequence[str] | None = None,
) -> tuple[dict[int, set[int]], list[str], set[frozenset[int]]]:
    """:func:`mating_graph`, with the pairs whose distance was measured as well.

    Kept apart so the public answer keeps its shape. Which pairs were measured is
    what tells a pair measured far apart from one nobody measured -- a probe that
    gave no number, or a pair the table leaves out -- and only the first may call
    a part loose.
    """
    neighbours: dict[int, set[int]] = {index: set() for index in range(part_count)}
    problems: list[str] = []
    measured: set[frozenset[int]] = set()
    for entry in gaps:
        pair = _pair(entry)
        if pair is None:
            problems.append(f"gap between two parts: unreadable entry {_quoted(entry)}")
            continue
        first, second, distance = pair
        if not (0 <= first < part_count and 0 <= second < part_count):
            # The gaps table disagrees with the part list, which is the same
            # corruption the unreadable case refuses. Ignoring it would let a
            # short table read as a fully measured one.
            problems.append(f"gap entry {_quoted(entry)} names a part that does not exist")
            continue
        if first == second:
            # No pair: it joins no part to another and keeps none apart. A pair
            # it stands in for is one the table leaves out, which is ruled on as
            # such.
            continue
        if _measurement(distance) is None:
            problems.append(
                f"{_GAP_UNCHECKED_PREFIX} {_at(labels, first)} and {_at(labels, second)}: "
                f"{distance!r} is not a distance that can be measured"
            )
            continue
        measured.add(frozenset((first, second)))
        if distance <= tolerance:
            neighbours[first].add(second)
            neighbours[second].add(first)
    return neighbours, problems, measured


def _check_mating(
    m: AssemblyMeasurements,
    spec: AssemblySpec,
    report: AssemblyReport,
    labels: Sequence[str],
    *,
    judge: bool,
) -> None:
    count = len(m.part_bboxes)
    tolerance = mating_tolerance(spec.clearance_mm)
    neighbours, problems, measured = _read_gaps(m.gaps, count, tolerance, labels)
    report.joints = [sorted(neighbours[index]) for index in range(count)]
    if not judge:
        return
    report.unchecked.extend(problems)

    # A part is called loose, or the parts apart, only on distances that were
    # measured: one nobody took -- a probe with no number, a budget spent, a pair
    # the table leaves out -- may join what the measured ones leave apart, and
    # blaming the geometry for it sends the repair after a joint that may be
    # sound. Where such a pair could join two groups the report says so instead,
    # so a short table never passes as one that measured the parts apart. The
    # joints described above stay the measured ones.
    groups, unknown = _possible_groups(_groups(neighbours, count), measured)
    if unknown is not None:
        (first, second), others = unknown
        more = f", and {others} other pair{'s' if others > 1 else ''}" if others else ""
        report.unchecked.append(
            "parts mating into one assembly: no distance was measured between "
            f"{_at(labels, first)} and {_at(labels, second)}{more}"
        )
    if len(groups) == 1:
        return
    alone = sorted(next(iter(group)) for group in groups if len(group) == 1)
    if alone:
        loose = ", ".join(_at(labels, index) for index in alone)
        report.failures.append(
            f"{loose} comes no closer than {_size1(tolerance)} mm to any other part; "
            "a part that mates with nothing is not joined to the assembly"
        )
    if len(groups) - len(alone) > 1:
        report.failures.append(
            f"the parts fall into {len(groups)} separate groups that do not meet each other, "
            "so they do not reassemble into one shape"
        )


def _is_permutation(order, count: int) -> bool:
    """Whether ``order`` is ``None`` or names every one of ``count`` parts once."""
    if order is None:
        return True
    return (
        isinstance(order, list)
        and all(type(index) is int for index in order)
        and sorted(order) == list(range(count))
    )


def _overlapping(m: AssemblyMeasurements, labels: Sequence[str]) -> bool:
    """Whether a readable entry of ``m.overlaps`` says two of its parts share volume.

    The ruling that every part comes out once each overlapping pair passes
    through each other is believed only beside an overlap the same payload
    shows, since the payload is whatever the script printed and the ruling sends
    the repair to geometry it names.
    """
    readable, _problems = _read_overlaps(m, labels)
    return any(shared > OVERLAP_EPSILON_MM3 for _first, _second, shared in readable)


def _check_order(m: AssemblyMeasurements, report: AssemblyReport, labels: Sequence[str]) -> None:
    """Rule on the order search's three outcomes, keeping the last two apart.

    A search that ran to completion and found nothing always names the parts it
    could not free, so ``order is None`` with an empty ``trapped`` means the
    search could not rule -- it ran out of budget, or stopped at pairs it could
    not see past or measure. Saying "no assembly order exists" there would send the
    model to fix geometry that was never measured, and it is the first line of
    the repair prompt -- which is exactly the confusion the module docstring
    promises this report does not make.
    """
    if m.order is not None:
        # An order that is not a permutation of the parts describes a different
        # assembly from the one measured. Type-checking the list is not enough:
        # an empty one is a list of ints and would otherwise read as "an order
        # was found" for a build with parts in it.
        if not _is_permutation(m.order, len(m.part_bboxes)):
            report.order = None
            report.unchecked.append(
                f"{_ORDER_UNCHECKED_PREFIX} the order given, {_quoted(m.order)}, is not a sequence "
                f"of all {len(m.part_bboxes)} parts"
            )
        return
    if m.trapped:
        if not all(type(index) is int and 0 <= index < len(m.part_bboxes) for index in m.trapped):
            # Named, a part that is not there sends the repair after it.
            report.unchecked.append(
                f"{_ORDER_UNCHECKED_PREFIX} the parts given as trapped, {_quoted(m.trapped)}, are not "
                f"all among the {len(m.part_bboxes)} parts"
            )
            return
        trapped = ", ".join(_at(labels, index) for index in sorted(m.trapped))
        if m.freed_without_overlaps is True and _overlapping(m, labels):
            # A part sharing volume with another is blocked every way it could
            # move, so the overlaps can leave no order on their own; told to
            # reorient a joint there, the repair changed what was never measured
            # wrong. Letting a pair pass through each other ignores every way the
            # two block each other, not only the volume they share, so this says
            # what comes first and not that nothing else is wrong.
            report.failures.append(
                f"no assembly order exists while parts share volume: {trapped} cannot be "
                "brought into place, a part that shares volume with another cannot move "
                "at all, and with each overlapping pair let pass through each other every "
                "part comes out; fix the shared volume first"
            )
            return
        report.failures.append(
            f"no assembly order exists: {trapped} cannot be brought into place without passing "
            "through another part; reorient the joint so it slides in along one axis"
        )
        return
    # The executor normally says why it could not rule, and that reason is
    # already on the report. A payload carrying neither a result nor a reason is
    # inconsistent, and this is the only thing between it and reading as a pass.
    if not any(
        isinstance(reason, str) and reason.startswith(_ORDER_UNCHECKED_PREFIX)
        for reason in m.unchecked
    ):
        report.unchecked.append(f"{_ORDER_UNCHECKED_PREFIX} no result and no reason was given")


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


def _possible_groups(
    apart: list[set[int]], measured: set[frozenset[int]]
) -> tuple[list[set[int]], tuple[tuple[int, int], int] | None]:
    """The groups the parts could still form, and a pair that could join two of ``apart``.

    ``apart`` are the groups the measured joints make. Two of them could still be
    one wherever some pair between them was not measured: only a pair measured far
    apart keeps two parts apart. So the joining runs over the groups rather than
    over every pair of parts, whose number grows with the square of the part
    count: two groups count as joinable while the measured pairs between them are
    fewer than all of them, and a group is held back only by a group it was
    measured apart from, so the work stays within the parts and the table.

    The second value is one pair between two groups that nobody measured, with how
    many others there are, or ``None`` where every pair between groups was.
    """
    of = {part: number for number, group in enumerate(apart) for part in group}
    between: Counter[tuple[int, int]] = Counter()
    for pair in measured:
        first, second = sorted(of[part] for part in pair)
        if first != second:
            between[first, second] += 1
    sizes = [len(group) for group in apart]
    remaining = list(range(len(apart)))
    possible: list[set[int]] = []
    joining: tuple[int, int] | None = None
    while remaining:
        members = [remaining.pop(0)]
        pending = list(members)
        while pending:
            here = pending.pop()
            held = []
            for there in remaining:
                key = (min(here, there), max(here, there))
                if between[key] < sizes[here] * sizes[there]:
                    joining = joining or key
                    members.append(there)
                    pending.append(there)
                else:
                    held.append(there)
            remaining = held
        possible.append(set().union(*(apart[number] for number in members)))
    if joining is None:
        return possible, None
    parts = sum(sizes)
    pairs = (parts * (parts - 1) - sum(size * (size - 1) for size in sizes)) // 2
    one, other = (sorted(apart[number]) for number in joining)
    example = next(
        (min(a, b), max(a, b)) for a in one for b in other if frozenset((a, b)) not in measured
    )
    return possible, (example, pairs - sum(between.values()) - 1)


def _pair(entry: object) -> tuple[int, int, float] | None:
    """An ``[i, j, value]`` entry as two part indices and a number, or ``None``.

    Checked rather than converted: ``int`` and ``float`` take a flag, a fraction
    and text as well, and each became a pair nobody measured -- a flag part 0 or
    1, a fraction the part below it, text whatever it spells, a character to a
    field where the entry itself was text.
    """
    if not isinstance(entry, list | tuple) or len(entry) != 3:
        return None
    first, second, value = entry
    if type(first) is not int or type(second) is not int:
        return None
    number = _number(value)
    return None if number is None else (first, second, number)


def _measurement(value: float) -> float | None:
    """``value`` if it can be a volume or distance the kernel measured, else ``None``.

    Neither is ever negative or unbounded, so a value that is was never measured,
    whoever produced it -- the kernel itself, or a worker of another build. Held
    to a threshold, NaN and the infinities read as a verdict either way, a pass
    or a fault, that no measurement gave. One rule for both halves, so a value
    the checks refuse is never one the measurement keeps.
    """
    return value if math.isfinite(value) and value >= 0.0 else None


def _index_list(value: object) -> list[int] | None:
    """``value`` as a list of part indices, or ``None`` when it is not one.

    Everything crossing the payload boundary is checked rather than trusted:
    an api and a worker on different builds disagree about shapes as well as
    field names, and an order that is a string used to read as a pass while a
    trapped list of strings raised out of a function documented never to.
    """
    if not isinstance(value, list):
        return None
    out: list[int] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int):
            return None
        out.append(item)
    return out


#: Each table a payload carries, by key, with what to call it where it cannot be
#: read: in the words of the check its absence leaves unmeasured, or ``None`` for
#: one no check reads.
_TABLES = {
    "part_bboxes": "the parts' bounding boxes",
    "overlaps": f"{_SHARED_UNCHECKED_PREFIX} the parts",
    "gaps": f"{_GAP_UNCHECKED_PREFIX} the parts",
    "unchecked": "which measurements could not be taken",
    "releases": None,
    "overlap_boxes": None,
}


def _listed(value: object) -> list | None:
    """``value`` where it is a list, ``[]`` where there is none, and ``None`` otherwise."""
    if value is None:
        return []
    return list(value) if isinstance(value, list) else None


def _ruling(value: object) -> bool | None:
    """``value`` where it is a yes or a no, and ``None`` -- nothing ruled -- otherwise.

    A one or a word is no ruling, and read as one it would send a repair to the
    overlaps on the strength of a value nobody measured.
    """
    return value if isinstance(value, bool) else None


def _overlap_places(boxes: list[list[float]]) -> dict[tuple[int, int], str]:
    """Where each pair's shared volume sits, for a sentence, by the pair it is for.

    An entry that cannot be read -- not eight items, an index that is no integer,
    a corner that is no finite number, or a box turned inside out -- is left out,
    so its pair is refused as it was before boxes were measured rather than
    pointed at a place nobody measured. One for a pair the overlaps do not hold
    is never looked up.
    """
    places: dict[tuple[int, int], str] = {}
    for entry in boxes:
        if not isinstance(entry, list | tuple) or len(entry) != 8:
            continue
        first, second, *corners = entry
        if type(first) is not int or type(second) is not int:
            continue
        numbers = [_number(corner) for corner in corners]
        if any(number is None or not math.isfinite(number) for number in numbers):
            continue
        low, high = numbers[:3], numbers[3:]
        if any(start > end for start, end in zip(low, high, strict=True)):
            continue
        decimals = _decimals(numbers)
        spans = ", ".join(
            f"{axis} {_coordinate(start, decimals)} to {_coordinate(end, decimals)}"
            for axis, start, end in zip("xyz", low, high, strict=True)
        )
        places[(min(first, second), max(first, second))] = (
            f", within {spans} in the script's coordinates"
        )
    return places


def _decimals(corners: list[float]) -> int:
    """How many decimals a box's corners are said to: about four figures of its
    largest, and never fewer than one.

    A fixed tenth read a box in a script written in metres as "x 0 to 0"; four
    figures say a millimetre box to a tenth and a metre box to a tenth of a
    millimetre alike, and drop the kernel's noise in both.
    """
    largest = max(abs(corner) for corner in corners)
    if largest == 0.0:
        return 1
    return max(1, 3 - math.floor(math.log10(largest)))


def _coordinate(value: float, decimals: int) -> str:
    """A coordinate for a sentence: rounded to ``decimals``, and never ``-0``."""
    return f"{round(value, decimals) + 0.0:g}"


#: How a value a payload sent is quoted in a sentence: enough of it to recognise,
#: and no more. The sentence goes on to the model and the panel, and the value is
#: whatever the code that printed the payload chose -- quoted whole, a table sent
#: as megabytes of text went into the repair prompt as megabytes of text.
_QUOTE = reprlib.Repr(
    maxlevel=3, maxtuple=6, maxlist=6, maxdict=6, maxset=6, maxstring=60, maxlong=40, maxother=60
)


def _quoted(value: object) -> str:
    return _QUOTE.repr(value)


def given_name(index: int, names: Sequence[str | None] | None) -> str | None:
    """The name a build gave part ``index``, on one line, or ``None`` where it gave none.

    ``unnamed(index)`` is no name: it is what a build calls a part it could not
    label, and read as one it would call the part by the number of the part
    before it. A label that shows nothing is no name either, nor one holding a
    character no name may hold, and a label over several lines is put on one, so
    a sentence naming it stays one sentence.
    """
    if names is None or not 0 <= index < len(names):
        return None
    name = names[index]
    if not isinstance(name, str):
        return None
    name = " ".join(name.split())
    if name == unnamed(index) or not readable(name):
        return None
    return name


def part_labels(count: int, names: Sequence[str | None] | None) -> list[str]:
    """What anything a person or the model reads calls each of ``count`` parts.

    A part's name where its build gave one, otherwise its number counted from
    one; and where two parts would read the same, each named one has its number
    beside its name, so two parts that share a name are told apart.
    """
    plain = [given_name(index, names) or f"part {index + 1}" for index in range(count)]
    seen = Counter(plain)
    return [
        f"{label} (part {index + 1})"
        if seen[label] > 1 and given_name(index, names) is not None
        else label
        for index, label in enumerate(plain)
    ]


def _at(labels: Sequence[str] | None, index: int) -> str:
    """Part ``index``'s label, or its number where ``labels`` does not reach it."""
    if labels is not None and 0 <= index < len(labels):
        return labels[index]
    return f"part {index + 1}"


def _size(size: tuple[float, float, float]) -> str:
    return " x ".join(_size1(value) for value in size)


def _size1(value: float) -> str:
    """A measurement for a sentence, rounded past the geometry's own noise."""
    return f"{round(value, 1):g}"


# TECH-DEBT: a consumer of engine releases still imports measure_assembly from this module / delete this forwarder once it imports from cadless.assembly_measure
_MOVED_TO_MEASURE = frozenset(
    {
        "measure_assembly",
        "ORDER_PROBE_BUDGET",
        "MEASUREMENT_TIME_SHARE",
        "MEASUREMENT_TIME_BUDGET_SECONDS",
    }
)


def __getattr__(name: str) -> object:
    """Resolve a name that moved to :mod:`cadless.assembly_measure`, importing it on first use."""
    if name in _MOVED_TO_MEASURE:
        from cadless import assembly_measure  # noqa: PLC0415 - lazy: that module imports this one

        return getattr(assembly_measure, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
