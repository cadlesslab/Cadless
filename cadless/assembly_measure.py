"""Measures a multi-part build on its live solids.

The numbers come back as an :class:`~cadless.assembly_check.AssemblyMeasurements`
for the policy in :mod:`cadless.assembly_check` to judge. ``build123d`` is imported
inside the functions rather than at module scope, so importing this module pulls in
no kernel.
"""

from __future__ import annotations

import math
import time
from collections.abc import Sequence

from cadless.assembly_check import (
    _GAP_UNCHECKED_PREFIX,
    _ORDER_UNCHECKED_PREFIX,
    _PAIRS_UNCHECKED_PREFIX,
    _SHARED_UNCHECKED_PREFIX,
    MATING_TOLERANCE_MOST_MM,
    OVERLAP_EPSILON_MM3,
    AssemblyMeasurements,
    _at,
    _measurement,
    declared_headings,
    part_labels,
)
from cadless.print_fit import _dimensions

#: The six axis directions every part is tried along first.
#:
#: Seam joints are made along an axis -- the split helper takes nothing else -- so
#: a correct interlock comes apart along one of these. They are not relied on
#: alone -- :func:`_candidate_directions` adds directions derived from the
#: geometry, because a candidate that turns out to be blocked costs kernel work
#: while a missing one costs a false refusal.
_AXES = (
    (1.0, 0.0, 0.0),
    (-1.0, 0.0, 0.0),
    (0.0, 1.0, 0.0),
    (0.0, -1.0, 0.0),
    (0.0, 0.0, 1.0),
    (0.0, 0.0, -1.0),
)

#: How many units of kernel work the measurement may spend.
#:
#: Each of these counts one: a pairwise overlap or distance, building a sweep,
#: one boolean of a sweep's piece against a part, a bore test's distance, a
#: walk's probe. The executor's wall clock covers the build, this measurement and
#: the export together, and a blown clock returns no summary at all rather than a
#: partial one -- so the search needs a bound of its own rather than borrowing
#: that one. It bounds the *number* of those and nothing else, and they differ
#: widely in cost -- ``MEASUREMENT_TIME_BUDGET_SECONDS`` beside it is what bounds
#: their duration. Running out is reported as an unestablished
#: check, never as a pass: work that stopped early has shown nothing.
#:
#: One budget covers the whole measurement, the pairs the kernel is asked about
#: included. Bounding only the order search left the O(n^2) phase ahead of it
#: free to spend the executor's entire wall clock on a model with very many
#: parts. A pair settled by its boxes spends none of it, which is what
#: ``MEASURED_PARTS_MOST`` is for.
ORDER_PROBE_BUDGET = 20_000

#: The most parts a build may have for its pairs to be measured.
#:
#: A pair settled by its boxes spends none of the budget above, and every pair
#: leaves an entry in the table of gaps however it was settled. So the probe
#: count does not bound how many pairs there are, and the clock alone lets that
#: table grow with the square of the part count -- held here, sent in the
#: payload and read again -- before it stops. Past this many parts nothing is
#: measured between them, and that is said before any pair is taken on.
#:
#: A bound on the parts rather than on the entries, because the pairs follow
#: from the parts alone and a build can be turned away before any of its pairs
#: is worked on. What sets it is what the table costs to hold and to send, not
#: what the kernel can measure: the clock and the probe count answer for that.
MEASURED_PARTS_MOST = 500

#: What share of the time left on the executor's wall clock the measurement
#: may spend.
#:
#: A second bound beside the probe count, because the two fail differently. The
#: count stops a model whose many parts each need the kernel; the clock stops a
#: single part whose topology makes one boolean operation take seconds. Counting
#: alone would rest on the units costing roughly the same, and they do not:
#: building a sweep is charged one unit though it cuts, prisms and checks every
#: face, and a pathological solid makes any one unit slow.
#:
#: A share rather than a fixed number of seconds, because the executor returns no
#: summary at all when its own clock runs out -- so this has to stop first, and a
#: constant only does that at one particular timeout. Left a fraction, lowering
#: the timeout tightens this with it instead of quietly inverting the two.
MEASUREMENT_TIME_SHARE = 1.0 / 3.0

#: The share applied to the default executor timeout, for a caller with no clock
#: to divide -- a direct call in a test, and nothing on the pipeline's path.
MEASUREMENT_TIME_BUDGET_SECONDS = 10.0

#: How far past a contact the walk looks for a clash, as a fraction of the thinnest
#: part dimension.
#:
#: Only the walk uses it, and only the pairs it walks -- those no sweep could
#: rule. The walk advances by however far apart the two
#: solids are, which is what makes it safe rather than sampled. At a contact the
#: separation is zero and bounds nothing, and the walk stops there: a probe this
#: far on can turn the heading into a measured clash, never into a clear one.
#:
#: It is not a step. A fixed step was tried first and is unsound: two 10 mm cubes
#: offset 0.5 mm across an oblique heading graze for 0.69 mm of travel, and a step
#: of half the thinnest dimension samples either side of that and calls the part
#: free. The interval a grazing contact occupies has nothing to do with how thick
#: either part is, so no fraction of a part's size bounds it.
_LOOK_AHEAD_FRACTION = 0.02

#: Separation at or below which two solids count as touching rather than apart.
#:
#: Not a tolerance on the answer but on the arithmetic: a distance query on two
#: faces that meet returns a value at kernel noise rather than exactly zero, and
#: advancing by that would crawl. Whether touching is a *clash* is decided by
#: shared volume, never by this. The bore test demands the same margin before it
#: calls a part clear of a bore's cylinder.
_CONTACT_MM = 1e-6


def measure_assembly(
    parts,
    probe_budget: int = ORDER_PROBE_BUDGET,
    time_budget: float = MEASUREMENT_TIME_BUDGET_SECONDS,
    names: Sequence[str | None] | None = None,
    scale: float = 1.0,
    order: Sequence[int] | None = None,
    releases: Sequence[Sequence[float]] | None = None,
    binds: bool = True,
) -> AssemblyMeasurements:
    """Measure the relations between ``parts``, which must already be in millimetres.

    The scaled solids, not the authoring-unit ones: the build volume this is
    checked against is in millimetres, and measuring the unscaled result would
    silently compare the two. ``scale`` is what the parts were scaled by from
    the script's own units, about the origin, and is used for one thing: where
    two parts overlap is said in the coordinates the script placed them at.

    ``order`` is an order the caller declares its parts go together in, each
    named by its place in ``parts``, and ``releases`` the heading each comes off
    along, ``[]`` in the slot of a part with none. Given an order, the parts are
    ruled in it and no order is searched for, unless ``binds`` is false and the
    order does not hold. Whatever is handed in is checked here as well as where
    a request is read, since this is called directly too.
    """
    boxes = [_extent(part) for part in parts]
    labels = part_labels(len(parts), names)
    unchecked: list[str] = []
    overlaps: list[list[float]] = []
    overlap_boxes: list[list[float]] = []
    gaps: list[list[float]] = []

    def stopped() -> AssemblyMeasurements:
        """What a measurement that stops at the pairs hands back: no order looked for.

        Said in a line of its own, since a report with no order and no word on
        why reads as a search that came back with nothing.
        """
        return AssemblyMeasurements(
            part_bboxes=boxes,
            overlaps=overlaps,
            gaps=gaps,
            order=None,
            trapped=[],
            unchecked=[
                *unchecked,
                f"{_ORDER_UNCHECKED_PREFIX} not looked for, as not every pair was measured",
            ],
            overlap_boxes=overlap_boxes,
            declared=(None if order is None else {"binds": bool(binds), "outcome": "unreached"}),
        )

    if len(parts) > MEASURED_PARTS_MOST:
        unchecked.append(
            f"{_PAIRS_UNCHECKED_PREFIX} the build has {len(parts)} parts and the check "
            f"measures the pairs of no more than {MEASURED_PARTS_MOST}"
        )
        return stopped()
    budget = _Budget(probe_budget, time_budget)
    scene = _Scene(parts)
    done = 0

    # Nothing is measured against a part whose box has no finite middle or size:
    # the kernel, handed one, can stay inside a single call past any budget, and
    # a budget cannot interrupt a call already running. The order search stops
    # at the same part, its look-ahead reading the size.
    unplaced = {index for index, part in enumerate(parts) if not _placed(part)}
    unchecked.extend(
        f"{_PAIRS_UNCHECKED_PREFIX} {_at(labels, index)}'s box has no finite middle or "
        "size, so nothing was measured against it"
        for index in sorted(unplaced)
    )
    for first in range(len(parts)):
        for second in range(first + 1, len(parts)):
            if first in unplaced or second in unplaced:
                continue
            try:
                budget.check()
                apart = scene.apart(first, second)
                if apart is None:
                    budget.spend()  # the shared-volume probe
                    budget.spend()  # the distance probe
            except _BudgetSpent:
                unchecked.append(
                    f"{_PAIRS_UNCHECKED_PREFIX} the measurement ran out of "
                    f"budget after {done} of "
                    f"{len(parts) * (len(parts) - 1) // 2} pairs"
                )
                return stopped()
            if apart is not None:
                # Boxes that hold them sit farther apart than any mating tolerance
                # reaches, so they share nothing and the kernel is not asked how
                # close they come.
                scene.at_rest[(first, second)] = 0.0
                gaps.append([first, second, apart])
                done += 1
                continue
            shared = _shared_volume(parts[first], parts[second])
            scene.at_rest[(first, second)] = shared
            if shared is None:
                unchecked.append(
                    f"{_SHARED_UNCHECKED_PREFIX} {_at(labels, first)} and "
                    f"{_at(labels, second)}: "
                    "the intersection could not be computed"
                )
            elif shared > 0.0:
                overlaps.append([first, second, shared])
                if shared > OVERLAP_EPSILON_MM3:
                    place = _shared_box(parts[first], parts[second], scale, budget)
                    if place is not None:
                        overlap_boxes.append([first, second, *place])

            done += 1
            distance = _distance(parts[first], parts[second])
            if distance is None:
                unchecked.append(
                    f"{_GAP_UNCHECKED_PREFIX} {_at(labels, first)} and "
                    f"{_at(labels, second)}: "
                    "the distance could not be computed"
                )
            else:
                gaps.append([first, second, distance])

    if order is None:
        found, declared = _disassembly_order(parts, budget, scene, labels), None
    else:
        found, declared = _declared_order(parts, budget, scene, labels, order, releases, binds)
    established, trapped, order_unchecked, proved = found
    unchecked.extend(order_unchecked)
    return AssemblyMeasurements(
        part_bboxes=boxes,
        overlaps=overlaps,
        gaps=gaps,
        order=established,
        trapped=trapped,
        unchecked=unchecked,
        releases=proved,
        overlap_boxes=overlap_boxes,
        freed_without_overlaps=(
            _freed_without_overlaps(parts, budget, scene, labels, overlaps) if trapped else None
        ),
        declared=declared,
    )


def _freed_without_overlaps(parts, budget, scene, labels, overlaps) -> bool | None:
    """Whether an order is found once each overlapping pair is let pass through the other.

    A part sharing volume with another is blocked every way it could move, so a
    build whose parts overlap can be trapped by its overlaps alone, and saying
    then that a joint needs reorienting sends the repair to what was never
    wrong. Asked again on what is left of the same budget, so a build that
    overlaps costs no more time than the measurement was given. It is asked of
    any build trapped while it overlaps, whether or not its order will be
    judged: the measurement is not told which checks a build faces, and the time
    is its own share either way. ``None`` where the second search could not
    rule: its reasons are about a build that does not exist, so they are not
    reported either.
    """
    partners: dict[int, set[int]] = {}
    for first, second, shared in overlaps:
        if shared > OVERLAP_EPSILON_MM3:
            partners.setdefault(first, set()).add(second)
            partners.setdefault(second, set()).add(first)
    if not partners:
        return None
    order, trapped, _unchecked, _releases = _disassembly_order(
        parts, budget, scene, labels, ignoring=partners
    )
    if order is not None:
        return True
    return False if trapped else None


def _extent(part) -> list[float]:
    size = part.bounding_box().size
    return [float(size.X), float(size.Y), float(size.Z)]


def _placed(part) -> bool:
    """Whether the part's box has a finite middle and size.

    Not where the part sits at a coordinate that is NaN or infinite, nor so far
    out that the middle of its box overflows though its corners do not.
    """
    box = part.bounding_box()
    centre, size = box.center(), box.size
    return all(
        math.isfinite(float(value))
        for value in (centre.X, centre.Y, centre.Z, size.X, size.Y, size.Z)
    )


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
        return _measurement(float(sum(solid.volume for solid in shared.solids())))
    except Exception:  # noqa: BLE001 - same: report rather than guess
        return None


def _shared_box(first, second, scale: float, budget: _Budget) -> list[float] | None:
    """The box around the volume two overlapping parts share, ``[x0, y0, z0, x1, y1,
    z1]`` in the script's own coordinates, or ``None`` where it cannot be had.

    Asked only of a pair measured to overlap past the noise the overlap check
    passes, so parts that only touch pay nothing for it, and charged to the
    budget like any other kernel work.
    The intersection is made again rather than kept from the volume probe, which
    every pair near enough to be measured makes and the order search reads; a
    build that overlaps is refused whatever, so the repeat costs only a build
    already failing. A pair overlapping in several places -- an axle through
    both walls of a bracket -- gets one box around all of them. ``None`` is not
    a fault: the overlap is still refused, only without saying where.
    """
    try:
        budget.spend()
    except _BudgetSpent:
        return None
    try:
        shared = first.intersect(second, include_touched=False)
        boxes = [solid.bounding_box() for solid in (shared.solids() if shared else [])]
    except Exception:  # noqa: BLE001 - a box nobody could compute leaves the sentence without one
        return None
    if not boxes:
        return None
    corners = [min(getattr(box.min, axis) for box in boxes) / scale for axis in "XYZ"] + [
        max(getattr(box.max, axis) for box in boxes) / scale for axis in "XYZ"
    ]
    return corners if all(math.isfinite(corner) for corner in corners) else None


def _distance(first, second) -> float | None:
    try:
        return _measurement(float(first.distance_to(second)))
    except Exception:  # noqa: BLE001 - same: report rather than guess
        return None


def _disassembly_order(parts, budget, scene=None, labels=None, ignoring=None):
    """An order in which the parts can be assembled, or why there is none.

    Found by taking the assembly apart: repeatedly free a part that can be
    translated clear of the rest, then reverse what that produced. An order found
    this way is one that works, because every move was checked against the solids
    rather than inferred. The converse does not hold -- a split that only comes
    apart along a curve, or by moving two parts at once, is refused here. That
    costs a repair attempt, and it is the price of an answer that is never wrong
    when it says yes.

    A round that frees nothing names its parts as trapped only when every heading
    was blocked by a clash it measured. Where some heading stopped at a contact it
    could not see past, or at a pair the kernel would not measure, nothing was
    shown trapped, so it says which parts and why instead -- a repair aimed at a
    trap that was never shown would change geometry nobody measured.

    ``ignoring`` maps a part to the parts it may pass through, for asking what
    order there would be if they did; nothing found that way is an order of the
    build as it stands.
    """
    ignoring = ignoring or {}
    if len(parts) < 2:
        return list(range(len(parts))), [], [], []

    look_ahead = _look_ahead(parts)
    if look_ahead is None:
        return None, [], [f"{_ORDER_UNCHECKED_PREFIX} a part has no measurable size"], []

    scene = scene or _Scene(parts)
    spent = (
        None,
        [],
        [f"{_ORDER_UNCHECKED_PREFIX} the search ran out of budget before it could rule"],
        [],
    )
    try:
        spheres = _spheres(scene, budget)
    except _BudgetSpent:
        return spent
    remaining = list(range(len(parts)))
    removed: list[int] = []
    releases: list[list[float]] = [[] for _ in parts]
    while remaining:
        freed = None
        scene.undecided.clear()
        scene.refused.clear()
        scene.blocking.clear()
        for index in remaining:
            passed = ignoring.get(index, ())
            others = [other for other in remaining if other != index and other not in passed]
            if not others:
                # The part left standing is what the rest come off, not one that
                # comes out itself. Nothing can block it, so every heading passes
                # on the first probe and recording one would file the order the
                # candidates happen to be listed in as a measurement. Asked with
                # ``ignoring``, a part left only with what it may pass through is
                # the same: nothing left can block it.
                freed = index
                break
            try:
                direction = _can_be_freed(parts, spheres, index, others, look_ahead, budget, scene)
            except _BudgetSpent:
                return spent
            if direction is not None:
                freed = index
                releases[index] = [float(direction.X), float(direction.Y), float(direction.Z)]
                break
        if freed is None:
            reasons = []
            if scene.refused:
                reasons.append(_unmeasured_reason(scene.refused, labels))
            if scene.undecided:
                reasons.append(_touching_reason(scene.undecided, labels))
            if reasons:
                return None, [], reasons, []
            return None, sorted(remaining), [], []
        remaining.remove(freed)
        removed.append(freed)
    # Removing in this order works, so assembling in the reverse of it does.
    return list(reversed(removed)), [], [], releases


def _declared_order(parts, budget, scene, labels, order, releases, binds):
    """Rule an order the caller declared, and say how it came out.

    Returns what :func:`_disassembly_order` returns, and beside it the record of
    the declared order. An order that binds is the answer however it came out,
    and nothing is searched for: an author's order that does not hold is a
    fault for the author to hear of, where an order found in its place would
    send the kit out with steps nobody wrote. One that does not bind is a hint.
    Where it does not hold the search runs as it would have with none given,
    and only the record says what became of it.
    """
    proved, record, reasons = _confirm_order(parts, budget, scene, labels, order, releases)
    record = {"binds": bool(binds), **record}
    if proved is not None:
        return (list(order), [], [], proved), record
    if binds:
        return (None, [], reasons, []), record
    return _disassembly_order(parts, budget, scene, labels), record


def _confirm_order(parts, budget, scene, labels, order, releases):
    """The heading each part was proved to come off along, or where the order stopped.

    Returns those headings, ``None`` where the order was not confirmed; the
    record's outcome and what goes with it; and, where nothing was ruled either
    way, the reasons.

    Each part after the first is ruled against the parts the order places
    before it and against no others, those being the ones in place when it goes
    on: along the heading declared for it where there is one, which is ruled
    once, and along the search's own candidates where there is none. The parts
    are taken in the order given and the first that is not free ends it, so
    what is reported is the first step an assembler could not make.

    A part is called blocked only where every heading tried met a clash that
    was measured, as the search calls a part trapped. Where one stopped at a
    contact it could not see past, or at a pair the kernel would not measure,
    the part is undecided, and the reasons say which pairs.
    """
    from build123d import Vector

    try:
        headings = declared_headings(order, releases, len(parts))
    except ValueError as mismatch:
        return (
            None,
            {"outcome": "mismatch"},
            [
                f"{_ORDER_UNCHECKED_PREFIX} the declared order does not describe the build: "
                f"{mismatch}"
            ],
        )
    if len(parts) < 2:
        return [], {"outcome": "confirmed"}, []
    look_ahead = _look_ahead(parts)
    if look_ahead is None:
        return (
            None,
            {"outcome": "unreached"},
            [f"{_ORDER_UNCHECKED_PREFIX} a part has no measurable size"],
        )
    proved: list[list[float]] = [[] for _ in parts]
    try:
        spheres = _spheres(scene, budget)
        for position in range(1, len(order)):
            index, before = order[position], list(order[:position])
            scene.undecided.clear()
            scene.refused.clear()
            scene.blocking.clear()
            heading = headings[index]
            if heading is None:
                freed = _can_be_freed(parts, spheres, index, before, look_ahead, budget, scene)
            else:
                freed = Vector(*heading)
                verdict = _rule_heading(
                    parts, spheres, index, before, freed, look_ahead, budget, scene
                )
                if verdict != _FREE:
                    freed = None
            if freed is None:
                return _stopped_at(index, heading, scene, labels)
            proved[index] = [float(freed.X), float(freed.Y), float(freed.Z)]
    except _BudgetSpent:
        return (
            None,
            {"outcome": "unreached"},
            [
                f"{_ORDER_UNCHECKED_PREFIX} the check ran out of budget before it could confirm "
                "the declared order"
            ],
        )
    return proved, {"outcome": "confirmed"}, []


def _stopped_at(index, heading, scene, labels):
    """What :func:`_confirm_order` answers for a part that was not shown free.

    Read off what the scene holds, which is this part's alone: the scene is
    cleared before each part is ruled.
    """
    reasons = []
    if scene.refused:
        reasons.append(_unmeasured_reason(scene.refused, labels))
    if scene.undecided:
        reasons.append(_touching_reason(scene.undecided, labels))
    if reasons:
        return None, {"outcome": "undecided", "part": index}, reasons
    blocked = {
        "outcome": "blocked",
        "part": index,
        "heading": [] if heading is None else list(heading),
        "blockers": sorted({other for _moving, other in scene.blocking}),
    }
    return None, blocked, []


def _unmeasured_reason(pairs, labels=None) -> str:
    """The unchecked line for an order stopped at pairs the kernel would not measure."""
    unordered = sorted({tuple(sorted(pair)) for pair in pairs})
    which = "; ".join(
        f"{_at(labels, first)} and {_at(labels, second)}" for first, second in unordered
    )
    return (
        f"{_ORDER_UNCHECKED_PREFIX} the geometry kernel could not measure {which} "
        "against each other, so nothing showed whether they come apart"
    )


def _touching_reason(pairs, labels=None) -> str:
    """The unchecked line for an order stopped at contacts it could not see past.

    Names each touching pair once, whichever of the two was moving, and says what
    would let the check rule: parts held apart by a gap are walked by the gap,
    never across a contact.
    """
    unordered = sorted({tuple(sorted(pair)) for pair in pairs})
    which = "; ".join(
        f"{_at(labels, first)} and {_at(labels, second)}" for first, second in unordered
    )
    return (
        f"{_ORDER_UNCHECKED_PREFIX} {which} touch where the check cannot follow their "
        "surfaces exactly, so it could not prove they come apart; a small gap between "
        "them lets it rule"
    )


def _can_be_freed(parts, spheres, index, others, look_ahead, budget, scene=None):
    """The heading this part comes out along, or ``None`` if none does.

    Returns the direction rather than a yes, because the one that worked is the
    only heading proved clear against the solids. Recovering it afterwards from
    the shape would be a guess, and wrong exactly where it matters -- a joint that
    releases along one axis only.
    """
    for direction in _candidate_directions(spheres, index, others):
        verdict = _rule_heading(parts, spheres, index, others, direction, look_ahead, budget, scene)
        if verdict == _FREE:
            return direction
    return None


#: What ruling one heading can come to. Three answers rather than a yes or no,
#: because "proved clear" and "not proved clear" are the only two a search that
#: is never wrong when it says yes can hand on, and "not proved" splits again into
#: blocked -- a clash it measured -- and undecided: a contact it could not see
#: past, or a pair the kernel would not measure.
_FREE = "free"
_BLOCKED = "blocked"
_UNDECIDED = "undecided"


def _rule_heading(parts, spheres, index, others, direction, look_ahead, budget, scene=None) -> str:
    """Rule whether one part slides along ``direction`` clear of every other.

    Decided on the volume the part sweeps over its whole travel rather than on
    positions sampled along it. A clash at any instant puts the part's interior
    inside another's at that instant, and that shared volume lies inside the
    sweep -- so a sweep that shares no volume with a part proves that no instant
    of the slide clashed with it, however briefly, which no stepped walk can
    promise.

    The sweep runs from where the part starts out to the farthest point any
    bounding sphere could still be met, so it covers every travel window at once.

    Only the relative motion matters: the part meeting another while it moves
    along the heading is the other meeting the part while it moves back along it.
    So where the moving part's sweep cannot rule a pair -- it has no exact one, or
    the kernel refused a boolean of it -- the other part is swept backwards
    instead, and only a pair no sweep could rule is walked.

    A pair where one part sits strictly inside a round bore of the other, about an
    axis along the heading, is settled before any of that: the two can never
    meet along that line, whatever surfaces the ends of the inner part have.
    """
    scene = scene or _Scene(parts)
    # A heading with no part in the way is ruled without a probe, and a build of
    # many parts far apart has many of those: the clock is read here so that it
    # stops them too.
    budget.check()
    centre, radius = spheres[index]
    windows = {}
    for other in others:
        other_centre, other_radius = spheres[other]
        window = _travel_window(centre, radius, other_centre, other_radius, direction)
        if window is not None:
            windows[other] = window
    if not windows:
        # No bounding sphere in the way anywhere along the heading, so no solid.
        return _FREE

    length = max(stop for _, stop in windows.values())
    own = _UNBUILT
    walked, refused = [], []
    for other in windows:
        if scene.nested(index, other, direction, budget):
            continue
        at_rest = scene.shared_at_rest(index, other, budget)
        if at_rest is None:
            # Nothing measured where they stand, so nothing past it either: not
            # free, and not a clash anybody saw.
            refused.append(other)
            continue
        if at_rest > OVERLAP_EPSILON_MM3:
            scene.blocking.add((index, other))
            return _BLOCKED
        if own is _UNBUILT:
            own = _sweep(parts[index], direction, length, budget)
        met = None
        if own is not None:
            met = _sweep_meets(own, parts[other], scene.reach(other), budget)
        if met is None:
            # No exact sweep of the moving part, or a boolean of it the kernel
            # refused: try the other part swept back.
            theirs = _sweep(parts[other], -direction, length, budget)
            if theirs is not None:
                met = _sweep_meets(theirs, parts[index], scene.reach(index), budget)
        if met:
            scene.blocking.add((index, other))
            return _BLOCKED
        if met is None:
            walked.append(other)
    undecided = []
    if walked:
        verdict, undecided, unmeasured, clashed = _walk(
            parts, {other: windows[other] for other in walked}, index, direction, look_ahead, budget
        )
        if verdict == _BLOCKED:
            scene.blocking.add((index, clashed))
            return _BLOCKED
        refused += unmeasured
    scene.undecided.update((index, other) for other in undecided)
    scene.refused.update((index, other) for other in refused)
    return _UNDECIDED if undecided or refused else _FREE


#: A sweep not built yet: built on first need, since a heading every other part
#: is excluded from needs none.
_UNBUILT = object()


class _Scene:
    """What the measurement works out once and reuses.

    A box that holds each part for certain, which the pairwise phase reads for
    every pair, and the kernel's box of it, which the sweeps, the bore test and
    the spheres the search prunes by read its reach from; the volume each pair
    shares where it stands, which the
    pairwise phase has already asked or read off the pair's boxes; each part's
    cylinder axes, and whether one of a pair sits inside a bore of the other
    along a line, which a heading and its reverse share and every round asks
    again; and the pairs the current round left undecided, which are what that
    round reports if it frees nothing, with the pairs it saw clash, which are
    what a declared order that stops there reports.
    """

    def __init__(self, parts) -> None:
        self.parts = parts
        self._nested: dict[tuple, bool] = {}
        self._axes: dict[int, list] = {}
        self._boxes: dict[int, object] = {}
        self._reach: dict[int, object] = {}
        #: The volume each pair ``(i, j)``, ``i < j``, shares where it stands --
        #: ``None`` where the kernel could not say. Filled by the pairwise phase,
        #: which asked already or saw the pair's boxes apart, so no heading asks
        #: again.
        self.at_rest: dict[tuple[int, int], float | None] = {}
        #: Pairs ``(moving, other)`` a walk stopped at undecided: touching where no
        #: sweep could rule them, so nothing showed the path past the contact clear
        #: or blocked.
        self.undecided: set[tuple[int, int]] = set()
        #: Pairs ``(moving, other)`` the kernel would not measure along a heading --
        #: refused where they stand, or by the walk once no sweep could rule them.
        self.refused: set[tuple[int, int]] = set()
        #: Pairs ``(moving, other)`` a heading was ruled blocked at: the two share
        #: volume where they stand, or were measured meeting on the way. One
        #: heading names one, the first met, since that ends its ruling.
        self.blocking: set[tuple[int, int]] = set()

    def shared_at_rest(self, first, second, budget) -> float | None:
        """The volume the two share where they stand, asked of the kernel once."""
        key = (min(first, second), max(first, second))
        if key not in self.at_rest:
            budget.spend()
            self.at_rest[key] = _shared_volume(self.parts[key[0]], self.parts[key[1]])
        return self.at_rest[key]

    def box(self, index):
        """A box the part cannot reach out of, taken once; ``None`` where none is known."""
        if index not in self._boxes:
            self._boxes[index] = _enclosing_box(_shape_of(self.parts[index]))
        return self._boxes[index]

    def reach(self, index):
        """The box the sweeps, the bore test and the pruning sphere read the part's reach from.

        Taken once, because reading it walks every face and edge of the part and
        a search asks for the same part's at every heading.
        """
        if index not in self._reach:
            self._reach[index] = _box_of(_shape_of(self.parts[index]))
        return self._reach[index]

    def apart(self, first, second) -> float | None:
        """How far apart the two parts' boxes sit, where that alone settles the pair.

        ``None`` where it does not: a part no box is known to hold, a distance
        that is no measurement, or boxes no farther apart than the largest mating
        tolerance, within which only the kernel says how close the parts come.
        Each box holds its part, so the distance between two is never more than
        the parts' own, and one past that tolerance is past any a gap is read
        against.

        The number answered is the one compared: growing a box by the tolerance
        and asking whether it clears the other reads a pair a hair past the
        tolerance as apart while the distance between the boxes, their margins
        counted, still sits inside it -- and that distance is what gets recorded.
        """
        boxes = self.box(first), self.box(second)
        if boxes[0] is None or boxes[1] is None:
            return None
        try:
            distance = _measurement(float(boxes[0].Distance(boxes[1])))
        except Exception:  # noqa: BLE001 - no distance, no shortcut: the pair is probed
            return None
        if distance is None or distance <= MATING_TOLERANCE_MOST_MM:
            return None
        return distance

    def axes(self, index):
        """The part's cylinder axes, read from its faces once."""
        if index not in self._axes:
            self._axes[index] = _cylinder_axes(self.parts[index])
        return self._axes[index]

    def nested(self, first, second, direction, budget) -> bool:
        """Whether one of the two sits strictly inside a round bore of the other along this line."""
        for line in _bore_lines(self.axes(first) + self.axes(second), direction):
            key = (min(first, second), max(first, second), line[2])
            if key not in self._nested:
                boxes = {first: self.reach(first), second: self.reach(second)}
                self._nested[key] = _one_inside_the_other(
                    self.parts, boxes, first, second, line, budget
                )
            if self._nested[key]:
                return True
        return False


def _sweep(part, direction, length, budget):
    """The pieces of the part's sweep, or ``None`` where there is no exact one to use.

    A surface this module has no exact cut for and a sweep the kernel would not
    build come to the same thing here: not a clash, and not a clear path either,
    only a pair to rule another way -- by sweeping the other part, or by walking.
    """
    budget.spend()
    try:
        return _sweep_pieces(part, direction, length)
    except _NoExactSweep:
        return None


def _sweep_meets(pieces, part, reach, budget) -> bool | None:
    """Whether any piece of a sweep shares volume with ``part``, whose box is ``reach``.

    ``None`` when the kernel could not tell -- no box for the part, or a boolean
    it refused -- which is neither a clash nor a clear path.
    """
    if reach is None:
        return None
    refused = False
    for prism, box in pieces:
        if box.IsOut(reach):
            # Disjoint enclosing boxes: the prism cannot share volume with it.
            continue
        budget.spend()
        shared = _prism_overlap(prism, part.wrapped)
        if shared is None:
            refused = True
        elif shared > OVERLAP_EPSILON_MM3:
            return True
    return None if refused else False


class _NoExactSweep(Exception):
    """Raised for a face whose sweep this module cannot build exactly."""


#: Below this, the cosine or sine of an angle between unit vectors counts as zero:
#: a planar face whose normal is this near square to the heading slides along
#: itself and sweeps no volume, and an axis this near the heading counts as
#: running along it. It absorbs the rounding a rotation leaves in a direction
#: meant to be exact, and nothing more.
_PARALLEL_COSINE = 1e-9

#: How far each box is grown at the least, in millimetres. The kernel's box
#: prefilters a sweep's booleans, sizes the sphere the search prunes by and sets
#: how far the bore test reaches along its axis; the box built from each face's
#: own bounds settles the pairs too far apart to mate, and is grown by the
#: shape's own tolerance besides. In each, erring wide costs a little work and
#: never an answer.
_BOX_MARGIN_MM = 0.01


def _sweep_pieces(part, direction, length):
    """The prisms whose union, with the part where it starts, is the volume it sweeps.

    Why these are enough: take any point the part passes over and the earliest
    instant the part covers it. Either that instant is the start, or the point is
    reached through the part's boundary at a spot that moves forward along the
    heading -- a face whose outward normal leads. Every such point therefore lies
    in the prism of a leading face, and every prism of a face lies inside the
    sweep because the face lies inside the part. Nothing in that needs the part to
    be convex: a bore or a pocket is covered like an outside wall.

    A planar face's normal is the same all over it, so it is ruled once and a
    trailing one dropped. A curved face can lead on one side and trail on the
    other, and folded over itself its prism is no solid at all, so it is first cut
    where its normal turns square to the heading, and every piece is kept -- a
    trailing piece's prism still lies inside the sweep, so keeping it costs a
    boolean and never an answer.

    ``None`` when there is no sweep to trust -- a part with no faces, a face that
    cannot be read, or a piece the kernel would not build; :class:`_NoExactSweep`
    for a surface this has no exact cut for.
    """
    from build123d import GeomType
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.gp import gp_Vec

    travel = gp_Vec(direction.X * length, direction.Y * length, direction.Z * length)
    pieces = []
    try:
        faces = list(part.faces())
        if not faces:
            # Nothing bounds it, so nothing says where it goes: not a sweep of no
            # volume but no sweep at all.
            return None
        for face in faces:
            kind = face.geom_type
            if kind == GeomType.PLANE:
                if face.normal_at(face.center()).dot(direction) <= _PARALLEL_COSINE:
                    continue
                segments, curved = [face.wrapped], False
            elif kind == GeomType.EXTRUSION and _runs_along(face.wrapped, direction):
                # A surface drawn straight along the heading slides along itself.
                continue
            elif kind in (GeomType.CYLINDER, GeomType.CONE, GeomType.SPHERE, GeomType.TORUS):
                segments, curved = _split_at_silhouette(face.wrapped, direction), True
                if segments is None:
                    return None
            else:
                raise _NoExactSweep(str(kind))
            for segment in segments:
                prism = _prism(segment, travel)
                if prism is None or (curved and not BRepCheck_Analyzer(prism).IsValid()):
                    return None
                box = _box_of(prism)
                if box is None:
                    return None
                pieces.append((prism, box))
    except _NoExactSweep:
        raise
    except Exception:  # noqa: BLE001 - geometry that cannot be read fails closed
        return None
    return pieces


def _prism(face, travel):
    """The solid ``face`` sweeps over ``travel``, the right way out, or ``None``.

    The kernel can hand a curved face's prism back inside out, and a boolean
    reads an inside-out solid as everything outside it: a part sitting wholly
    inside the prism would share none of its volume and pass as clear, while one
    nowhere near it would be read as a clash. Turned so its volume is positive,
    the prism means what it encloses. One with no volume is no sweep at all, nor
    is one whose volume is not finite: NaN says neither which way it faces, and no
    face swept a finite distance encloses an unbounded volume.
    """
    from OCP.BRepGProp import BRepGProp
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.GProp import GProp_GProps

    prism = BRepPrimAPI_MakePrism(face, travel).Shape()
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(prism, props)
    volume = props.Mass()
    if volume == 0.0 or not math.isfinite(volume):
        return None
    return prism.Reversed() if volume < 0.0 else prism


def _split_at_silhouette(face, direction):
    """A curved face cut where its normal turns square to ``direction``.

    Cut there, every piece leads or trails all over, so it projects onto the
    plane across the heading without folding -- which is what makes its prism a
    solid. On each surface handled here that silhouette lies in planes, so cutting
    by those planes is exact, not an approximation of it.

    An empty list for a face that slides along itself and sweeps nothing; ``None``
    when the kernel could not cut it.
    """
    from OCP.BRepAdaptor import BRepAdaptor_Surface

    planes = _silhouette_planes(BRepAdaptor_Surface(face), direction)
    if planes is None:
        return []
    if not planes:
        return [face]
    return _cut_by_planes(face, planes)


def _silhouette_planes(surface, direction):
    """The ``(point, normal)`` planes holding a surface's silhouette along ``direction``.

    ``None`` for a surface that slides along itself; an empty list where the normal
    never turns square to the heading. Per surface, with ``a`` a cone's half-angle:

    * cylinder and cone -- the normal depends only on the angle round the axis,
      ``cos(a) * radial - sin(a) * axis``, and is square to the heading where
      ``cos(u - phi) = tan(a) * along / across``. Each such ``u`` is a straight
      ruling lying in the plane through the axis with normal ``tangent(u)``.
    * sphere -- the great circle in the plane through the centre square to the
      heading.
    * torus with its axis along the heading -- the normal is square to the axis
      only on the two equators, both in the plane through the centre square to
      the axis.
    * torus with its axis square to the heading -- in the torus's angles the
      normal's component along the heading is ``cos(v) * cos(u - phi)``, which
      vanishes where ``cos(v) = 0`` -- the two circles a minor radius either side
      of the centre plane -- or where ``u = phi +/- 90 degrees``, in the plane
      through the axis square to the heading.

    A torus at any other angle has a silhouette that lies in no plane, and raises
    :class:`_NoExactSweep` like every other surface.
    """
    from OCP.GeomAbs import GeomAbs_Cone, GeomAbs_Cylinder, GeomAbs_Sphere, GeomAbs_Torus
    from OCP.gp import gp_Vec

    heading = gp_Vec(direction.X, direction.Y, direction.Z)
    kind = surface.GetType()
    if kind == GeomAbs_Sphere:
        return [(surface.Sphere().Location(), heading)]
    if kind == GeomAbs_Torus:
        torus = surface.Torus()
        frame = torus.Position()
        axis = gp_Vec(frame.Direction())
        along = heading.Dot(axis)
        if heading.Crossed(axis).Magnitude() <= _PARALLEL_COSINE:
            return [(frame.Location(), axis)]
        if abs(along) <= _PARALLEL_COSINE:
            minor = torus.MinorRadius()
            centre = frame.Location()
            return [
                (centre.Translated(axis * minor), axis),
                (centre.Translated(axis * -minor), axis),
                (centre, heading),
            ]
        raise _NoExactSweep("a torus oblique to the heading")
    if kind == GeomAbs_Cylinder:
        frame, half_angle = surface.Cylinder().Position(), 0.0
    elif kind == GeomAbs_Cone:
        cone = surface.Cone()
        frame, half_angle = cone.Position(), cone.SemiAngle()
    else:
        raise _NoExactSweep("a curved face with no exact cut")

    x, y = gp_Vec(frame.XDirection()), gp_Vec(frame.YDirection())
    dx, dy = heading.Dot(x), heading.Dot(y)
    along = heading.Dot(gp_Vec(frame.Direction()))
    across = math.hypot(dx, dy)
    if across <= _PARALLEL_COSINE:
        # Heading along the axis: a cylinder slides along itself, and a cone's
        # normal keeps one sign all round. Either way there is no ruling to cut at.
        return None if half_angle == 0.0 else []
    ratio = math.tan(half_angle) * along / across
    if abs(ratio) >= 1.0:
        return []
    phi = math.atan2(dy, dx)
    spread = math.acos(ratio)
    normals = []
    for u in (phi + spread, phi - spread):
        normal = x * -math.sin(u) + y * math.cos(u)
        # A cylinder's two rulings lie in one plane; cutting by it twice is waste.
        if not any(normal.Crossed(seen).Magnitude() <= _PARALLEL_COSINE for seen in normals):
            normals.append(normal)
    return [(frame.Location(), normal) for normal in normals]


def _runs_along(face, direction) -> bool:
    """Whether a linear-extrusion face is drawn straight along ``direction``."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.gp import gp_Vec

    drawn = gp_Vec(BRepAdaptor_Surface(face).Direction())
    return drawn.Crossed(gp_Vec(direction.X, direction.Y, direction.Z)).Magnitude() <= (
        _PARALLEL_COSINE
    )


def _cut_by_planes(face, planes):
    """``face`` cut by every ``(point, normal)`` plane, as a list of faces, or ``None``."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Splitter
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.gp import gp_Dir, gp_Pln
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopTools import TopTools_ListOfShape

    tools = TopTools_ListOfShape()
    for point, normal in planes:
        tools.Append(BRepBuilderAPI_MakeFace(gp_Pln(point, gp_Dir(normal))).Face())
    arguments = TopTools_ListOfShape()
    arguments.Append(face)
    splitter = BRepAlgoAPI_Splitter()
    splitter.SetArguments(arguments)
    splitter.SetTools(tools)
    try:
        splitter.Build()
    except Exception:  # noqa: BLE001 - a cut that did not happen fails closed
        return None
    if not splitter.IsDone():
        return None
    out = []
    explorer = TopExp_Explorer(splitter.Shape(), TopAbs_FACE)
    while explorer.More():
        out.append(explorer.Current())
        explorer.Next()
    return out or None


def _box_of(shape):
    """The kernel's box of the shape, widened to hold every control point and grown by
    the margin, or ``None`` if none could be taken.

    The kernel's tight bound, taken from the exact geometry. The quick bound
    samples some surfaces -- a surface of revolution among them -- and its box
    can sit inside the surface, which would let the prefilter skip a boolean that
    clashes. Never from a mesh the shape may carry either: a mesh cuts across a
    curved face, and a box taken from it could miss the face.

    The tight bound of a B-spline or Bezier face or edge is found by a search,
    and the search can stop short of the surface: a box taken so sat inside a
    ball whose faces are B-splines. Whether it does depends on the build the
    kernel runs on -- the same version stopped short on one machine and reached
    the ball on another -- so a machine where it reaches shows nothing about
    the rest. The box is therefore widened to hold the control points of every
    such face and edge, which the geometry cannot leave, and on a free-form
    part it is tight no longer. That costs the prefilter some booleans there.
    The box :func:`_enclosing_box` builds would cost it more: that one bounds a
    sphere and a torus whole, however small the piece of one a face is.

    The other kinds the kernel bounds by a search -- a surface turned about an
    axis from a free-form profile, an offset surface -- have no control points
    to be held by, and are left to it.
    """
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    if shape is None:
        return None
    box = Bnd_Box()
    try:
        BRepBndLib.AddOptimal_s(shape, box, False, False)
        for surface in _surfaces(shape):
            _hold_surface_poles(surface, box)
        for curve in _curves(shape):
            _hold_curve_poles(curve, box)
    except Exception:  # noqa: BLE001 - no box, no prefilter: the caller fails closed
        return None
    if box.IsVoid():
        return None
    box.Enlarge(_BOX_MARGIN_MM)
    return box


def _enclosing_box(shape):
    """A box the shape cannot reach out of, grown by the margin, or ``None`` where none is known.

    What settles a pair without a probe has to hold the part for certain, so
    this takes nothing from a search over a surface. Each face and edge is
    bounded by what bounds its kind outright:

    - a plane, a cylinder, a cone and a surface drawn along a straight line are
      made of straight lines that end on the face's edges, so the edges bound the
      face;
    - a sphere and a torus are bounded whole, whatever part of one the face is;
    - a B-spline or Bezier surface or curve lies among its control points;
    - a straight edge lies between its ends, and an arc of a circle or an ellipse
      has a closed form.

    Any other kind -- a surface turned about an axis from a free-form profile, an
    offset surface -- has no such bound here, and neither has a face of the first
    kind with no edge to end on; the shape then has no box, and its pairs are
    measured. The margin is widened by the loosest tolerance the shape carries,
    since an edge may sit that far off the face it bounds.

    A solid turned inside out has no box either. The kernel takes it for
    everything outside its shell, and says of a part standing well clear of that
    shell that the two share the whole of the part; a box drawn round the shell
    holds none of that.
    """
    from OCP.Bnd import Bnd_Box
    from OCP.BndLib import BndLib
    from OCP.BRep import BRep_Tool
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.GeomAbs import (
        GeomAbs_Circle,
        GeomAbs_Cone,
        GeomAbs_Cylinder,
        GeomAbs_Ellipse,
        GeomAbs_Line,
        GeomAbs_Plane,
        GeomAbs_Sphere,
        GeomAbs_SurfaceOfExtrusion,
        GeomAbs_Torus,
    )
    from OCP.Precision import Precision
    from OCP.ShapeAnalysis import ShapeAnalysis_ShapeTolerance
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_OUT, TopAbs_SOLID, TopAbs_VERTEX
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    if shape is None:
        return None
    box = Bnd_Box()
    try:
        # Every solid as it is held, not each distinct one once: a solid held the
        # right way out and again inside out is one solid to a map of shapes.
        solids = TopExp_Explorer(shape, TopAbs_SOLID)
        while solids.More():
            beyond = BRepClass3d_SolidClassifier(solids.Current())
            beyond.PerformInfinitePoint(Precision.Confusion_s())
            if beyond.State() != TopAbs_OUT:
                return None
            solids.Next()
        for surface in _surfaces(shape):
            kind = surface.GetType()
            if kind in (GeomAbs_Plane, GeomAbs_Cylinder, GeomAbs_Cone, GeomAbs_SurfaceOfExtrusion):
                if not TopExp_Explorer(surface.Face(), TopAbs_EDGE).More():
                    return None
            elif kind == GeomAbs_Sphere:
                ball = surface.Sphere()
                _hold_about(ball.Location(), [ball.Radius()] * 3, box)
            elif kind == GeomAbs_Torus:
                ring = surface.Torus()
                axis = ring.Axis().Direction()
                # The ring's middle circle reaches this far along each axis, and
                # the tube stands its own radius off that circle all round.
                reach = [
                    ring.MajorRadius() * math.sqrt(max(0.0, 1.0 - along * along))
                    + ring.MinorRadius()
                    for along in (axis.X(), axis.Y(), axis.Z())
                ]
                _hold_about(ring.Location(), reach, box)
            elif not _hold_surface_poles(surface, box):
                return None
        for curve in _curves(shape):
            kind = curve.GetType()
            if kind == GeomAbs_Line:
                box.Add(curve.Value(curve.FirstParameter()))
                box.Add(curve.Value(curve.LastParameter()))
            elif kind in (GeomAbs_Circle, GeomAbs_Ellipse):
                conic = curve.Circle() if kind == GeomAbs_Circle else curve.Ellipse()
                arc = Bnd_Box()
                BndLib.Add_s(conic, curve.FirstParameter(), curve.LastParameter(), 0.0, arc)
                box.Add(arc)
            elif not _hold_curve_poles(curve, box):
                return None
        for vertex in _each(shape, TopAbs_VERTEX):
            box.Add(BRep_Tool.Pnt_s(TopoDS.Vertex_s(vertex)))
        loosest = _measurement(float(ShapeAnalysis_ShapeTolerance().Tolerance(shape, 1)))
    except Exception:  # noqa: BLE001 - no box, no shortcut: the pair is measured
        return None
    if box.IsVoid() or loosest is None:
        return None
    box.Enlarge(_BOX_MARGIN_MM + loosest)
    return box


def _shape_of(part):
    """The kernel's shape under ``part``, or ``None`` where it has none to give."""
    try:
        return part.wrapped
    except Exception:  # noqa: BLE001 - a part with nothing in it has no box
        return None


def _each(shape, kind):
    """Every sub-shape of ``kind`` in ``shape``, once however many others share it."""
    from OCP.TopExp import TopExp
    from OCP.TopTools import TopTools_IndexedMapOfShape

    found = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, kind, found)
    return [found.FindKey(index) for index in range(1, found.Extent() + 1)]


def _surfaces(shape):
    """The surface of every face of ``shape``, where the shape places it."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopoDS import TopoDS

    return [BRepAdaptor_Surface(TopoDS.Face_s(face), False) for face in _each(shape, TopAbs_FACE)]


def _curves(shape):
    """The curve of every edge of ``shape`` that has one, where the shape places it.

    An edge shrunk to a point -- the tip of a cone, the pole of a sphere -- has
    none, and its vertex stands for it.
    """
    from OCP.BRep import BRep_Tool
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopoDS import TopoDS

    edges = [TopoDS.Edge_s(edge) for edge in _each(shape, TopAbs_EDGE)]
    return [BRepAdaptor_Curve(edge) for edge in edges if not BRep_Tool.Degenerated_s(edge)]


def _hold_about(centre, reach, box) -> None:
    """Widen ``box`` to hold what reaches ``reach`` along each axis either side of ``centre``."""
    box.Update(
        centre.X() - reach[0],
        centre.Y() - reach[1],
        centre.Z() - reach[2],
        centre.X() + reach[0],
        centre.Y() + reach[1],
        centre.Z() + reach[2],
    )


def _hold_surface_poles(surface, box) -> bool:
    """Widen ``box`` to hold a B-spline or Bezier surface's control points.

    ``False`` for any other surface, which has none.
    """
    from OCP.GeomAbs import GeomAbs_BezierSurface, GeomAbs_BSplineSurface

    kind = surface.GetType()
    if kind == GeomAbs_BSplineSurface:
        poles = surface.BSpline().Poles()
    elif kind == GeomAbs_BezierSurface:
        poles = surface.Bezier().Poles()
    else:
        return False
    for row in range(poles.LowerRow(), poles.UpperRow() + 1):
        for column in range(poles.LowerCol(), poles.UpperCol() + 1):
            box.Add(poles.Value(row, column))
    return True


def _hold_curve_poles(curve, box) -> bool:
    """Widen ``box`` to hold a B-spline or Bezier curve's control points.

    ``False`` for any other curve, which has none.
    """
    from OCP.GeomAbs import GeomAbs_BezierCurve, GeomAbs_BSplineCurve

    kind = curve.GetType()
    if kind == GeomAbs_BSplineCurve:
        poles = curve.BSpline().Poles()
    elif kind == GeomAbs_BezierCurve:
        poles = curve.Bezier().Poles()
    else:
        return False
    for index in range(poles.Lower(), poles.Upper() + 1):
        box.Add(poles.Value(index))
    return True


def _prism_overlap(prism, other) -> float | None:
    """The volume one prism of a sweep shares with another part, or ``None``.

    Straight to the kernel rather than through :func:`_shared_volume`, skipping
    the clean-up pass that function runs, which a sweep of many pieces would pay
    for once per piece.
    """
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    from OCP.TopAbs import TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer

    try:
        common = BRepAlgoAPI_Common(other, prism)
        if not common.IsDone():
            return None
        total = 0.0
        explorer = TopExp_Explorer(common.Shape(), TopAbs_SOLID)
        while explorer.More():
            props = GProp_GProps()
            BRepGProp.VolumeProperties_s(explorer.Current(), props)
            total += abs(props.Mass())
            explorer.Next()
        return _measurement(total)
    except Exception:  # noqa: BLE001 - same: report rather than guess
        return None


#: Digits a heading is rounded to before it keys a line: rounding noise in a
#: direction meant to be exact must not split one line into two.
_HEADING_KEY_DECIMALS = 12

#: Digits, in millimetres, an axis's position is rounded to before it keys a
#: line, so the cylinders of one bore -- split at a seam or by a cut -- share one.
_AXIS_KEY_DECIMALS = 6

#: How far past a part's ends the bore test's axis segment, tube and cylinder
#: reach, in millimetres. Any positive margin does; it only keeps them clear of
#: the ends.
_SPAN_MARGIN_MM = 1.0

#: The tolerance the bore test's tube is built with -- the kernel's own confusion
#: distance, below which it treats two points as one.
_TUBE_TOLERANCE_MM = 1e-7


def _cylinder_axes(part):
    """``(point, direction)`` for the axis of each of the part's cylindrical faces."""
    from build123d import Vector
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder

    try:
        faces = list(part.faces())
    except Exception:  # noqa: BLE001 - no faces to read, no bore to find
        return []
    axes = []
    for face in faces:
        surface = BRepAdaptor_Surface(face.wrapped)
        if surface.GetType() != GeomAbs_Cylinder:
            continue
        axis = surface.Cylinder().Axis()
        spot, along = axis.Location(), axis.Direction()
        axes.append((Vector(spot.X(), spot.Y(), spot.Z()), Vector(along.X(), along.Y(), along.Z())))
    return axes


def _bore_lines(axes, direction):
    """Of ``axes``, the lines that run along the heading, each once.

    Each comes as ``(point, heading, key)``: a point on the axis, the heading with
    its sign fixed, and a key a heading and its reverse share -- which way along a
    line a part slides changes nothing about whether it sits inside a bore there.
    """
    from build123d import Vector

    heading = Vector(*_line_key(direction)).normalized()
    lines = {}
    for point, along in axes:
        if along.cross(heading).length > _PARALLEL_COSINE:
            continue
        point = point - heading * point.dot(heading)
        spot_key = tuple(round(v, _AXIS_KEY_DECIMALS) for v in (point.X, point.Y, point.Z))
        key = (_line_key(heading), spot_key)
        lines.setdefault(key, (point, heading, key))
    return list(lines.values())


def _one_inside_the_other(parts, boxes, first, second, line, budget) -> bool:
    """Whether one part lies inside a round bore, about ``line``, of the other.

    ``boxes`` holds each of the two parts' boxes, by its index.

    Distances only place a test cylinder: the outer part's nearest approach to the
    axis is its bore, and the cylinder goes halfway across the gap the inner part
    leaves to it. The proof is by volume -- none of the inner part outside the
    cylinder, none of the outer part inside it, over a stretch of axis spanning
    both. Their shadows across the line, one inside a circle and the other
    outside it, then never meet however far either slides.

    By volume rather than by distance because a distance search can miss the
    point where a curved face crosses a cylinder away from any of its edges, and
    so report a gap that is not there; a boolean does not depend on finding that
    point. A distance that misleads only misplaces the cylinder, and a misplaced
    cylinder fails one of the two booleans rather than passing both.
    """
    point, heading, _ = line
    spans = {index: _span(boxes[index], heading) for index in (first, second)}
    reach = {}
    for index in (first, second):
        segment = _axis_segment(point, heading, spans[index])
        if segment is None:
            return False
        budget.spend()
        reach[index] = _distance(parts[index], segment)
        if reach[index] is None:
            return False
    inner, outer = sorted((first, second), key=lambda index: reach[index])
    bore = reach[outer]
    if reach[inner] >= bore - _CONTACT_MM:
        return False
    tube = _tube(point, heading, bore, spans[inner])
    if tube is None:
        return False
    budget.spend()
    gap = _distance(parts[inner], tube)
    if gap is None or gap <= _CONTACT_MM:
        return False
    both = (min(spans[first][0], spans[second][0]), max(spans[first][1], spans[second][1]))
    rod = _rod(point, heading, bore - gap / 2, both)
    if rod is None:
        return False
    budget.spend()
    outside = _cut_volume(parts[inner], rod)
    if outside is None or outside > OVERLAP_EPSILON_MM3:
        return False
    budget.spend()
    inside = _shared_volume(parts[outer], rod)
    return inside is not None and inside <= OVERLAP_EPSILON_MM3


def _line_key(direction) -> tuple[float, float, float]:
    """``direction`` with its sign fixed, so a heading and its reverse share a key."""
    values = (float(direction.X), float(direction.Y), float(direction.Z))
    sign = next(
        (1.0 if value > 0 else -1.0 for value in values if abs(value) > _PARALLEL_COSINE), 1.0
    )
    return tuple(round(sign * value, _HEADING_KEY_DECIMALS) for value in values)


def _span(box, heading, margin: float = _SPAN_MARGIN_MM):
    """How far along ``heading`` a part with this box reaches, with ``margin`` beyond each end."""
    if box is None:
        return None
    low = box.Get()
    ends = [
        x * heading.X + y * heading.Y + z * heading.Z
        for x in (low[0], low[3])
        for y in (low[1], low[4])
        for z in (low[2], low[5])
    ]
    return min(ends) - margin, max(ends) + margin


def _axis_segment(point, heading, span):
    """The stretch of the axis through ``point`` that ``span`` covers."""
    from build123d import Edge

    if span is None:
        return None
    return Edge.make_line(point + heading * span[0], point + heading * span[1])


def _tube(point, heading, radius, span):
    """A closed cylindrical face of ``radius`` about the axis, over ``span``."""
    from build123d import Face
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.Geom import Geom_CylindricalSurface
    from OCP.gp import gp_Ax3, gp_Dir, gp_Pnt

    if span is None or radius <= _CONTACT_MM:
        return None
    base = point + heading * span[0]
    surface = Geom_CylindricalSurface(
        gp_Ax3(gp_Pnt(base.X, base.Y, base.Z), gp_Dir(heading.X, heading.Y, heading.Z)), radius
    )
    maker = BRepBuilderAPI_MakeFace(
        surface, 0.0, 2.0 * math.pi, 0.0, span[1] - span[0], _TUBE_TOLERANCE_MM
    )
    if not maker.IsDone():
        return None
    return Face(maker.Face())


def _rod(point, heading, radius, span):
    """A solid cylinder of ``radius`` about the axis, over ``span``, or ``None``."""
    from build123d import Plane, Solid

    if span is None or radius <= _CONTACT_MM:
        return None
    try:
        base = point + heading * span[0]
        return Solid.make_cylinder(radius, span[1] - span[0], Plane(origin=base, z_dir=heading))
    except Exception:  # noqa: BLE001 - no cylinder, no proof: the caller declines
        return None


def _cut_volume(part, tool) -> float | None:
    """The volume of ``part`` left outside ``tool``, or ``None``.

    ``None`` where it could not be cut, or where the volume it gave is no measurement.
    """
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    from OCP.TopAbs import TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer

    try:
        cut = BRepAlgoAPI_Cut(part.wrapped, tool.wrapped)
        if not cut.IsDone():
            return None
        total = 0.0
        explorer = TopExp_Explorer(cut.Shape(), TopAbs_SOLID)
        while explorer.More():
            props = GProp_GProps()
            BRepGProp.VolumeProperties_s(explorer.Current(), props)
            total += abs(props.Mass())
            explorer.Next()
        return _measurement(total)
    except Exception:  # noqa: BLE001 - same: report rather than guess
        return None


def _walk(parts, windows, index, direction, look_ahead, budget):
    """Walk one part along the heading past the others in ``windows``.

    For the pairs no sweep could rule. It advances by the
    measured separation and by nothing more: moving a solid less than its
    distance to another cannot bring the two into contact, so a step that size
    passes over nothing. At contact the separation bounds nothing and no fixed
    step is safe -- a clash can begin and end inside any step -- so the walk never
    steps across one. It looks for a clash at the contact and ``look_ahead`` past
    it, and answers ``_BLOCKED`` if either probe finds one and ``_UNDECIDED`` if
    neither does: the look-ahead can turn an open question into a measured clash,
    never into a clear path. A probe the kernel refuses leaves its pair
    unmeasured, which is undecided too, never blocked: nothing was seen to clash.

    Walks against several others advance in turn, so one that blocks near the
    start ends the heading before a long walk past another is paid for. Returns
    the verdict; when it is undecided, the other parts the walk stopped at a
    contact and those it could not measure; and, when it is blocked, the part
    the clash was found with.
    """
    from build123d import Location

    part = parts[index]
    walks = [[start, stop, other] for other, (start, stop) in windows.items()]
    undecided, refused = [], []
    while walks:
        for walk in list(walks):
            position, stop, other = walk
            if position > stop:
                walks.remove(walk)
                continue
            budget.spend()
            separation = _distance(part.moved(Location(direction * position)), parts[other])
            if separation is None:
                # A probe the kernel refused: this pair goes unmeasured from here,
                # which frees nothing and blames nothing.
                walks.remove(walk)
                refused.append(other)
                continue
            if separation > _CONTACT_MM:
                walk[0] = position + separation
                continue
            looked = []
            for ahead in (position, position + look_ahead):
                budget.spend()
                shared = _shared_volume(part.moved(Location(direction * ahead)), parts[other])
                if shared is not None and shared > OVERLAP_EPSILON_MM3:
                    return _BLOCKED, [], [], other
                looked.append(shared)
            walks.remove(walk)
            (refused if None in looked else undecided).append(other)
    verdict = _UNDECIDED if undecided or refused else _FREE
    return verdict, undecided, refused, None


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
    ruled against the solids -- swept or walked -- rather than believed.
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


def _candidate_directions(spheres, index, others):
    """The six axes, then a direction leading away from each remaining part.

    The geometry-derived ones cover a joint whose slide axis is not aligned to an
    axis. Adding a candidate can only remove a false refusal, never create a false
    pass: every direction offered is still checked against the solids, so one that
    is not really free is rejected when it is ruled.

    Handed out one at a time, each worked out only when the one before it has
    been ruled. A part that leaves along an axis never needs the rest, and
    listing them all first cost the square of the part count for every part
    tried, before the search had ruled a heading or read its clock.
    """
    from build123d import Vector

    directions = [Vector(*axis) for axis in _AXES]
    yield from tuple(directions)
    centre = Vector(*spheres[index][0])
    for other in others:
        away = centre - Vector(*spheres[other][0])
        if away.length > 1e-9:
            away = away.normalized()
            # A heading already handed out would only be ruled a second time.
            if all((away - known).length > 1e-9 for known in directions):
                directions.append(away)
                yield away


def _spheres(scene, budget):
    """Each part's bounding sphere, in part order.

    The clock is read before each. Taking a part's box walks all its faces and
    edges, and with many such parts the search would spend its time here before
    it ruled a heading, the clock unread.
    """
    spheres = []
    for index, part in enumerate(scene.parts):
        budget.check()
        spheres.append(_sphere(part, scene.reach(index)))
    return spheres


def _sphere(part, reach):
    """A part's bounding-sphere centre and radius, for the cheap pruning test.

    Centred on the middle of the box the part gives of itself, and wide enough
    to hold ``reach``, the box the sweeps read, where the part has one. The box
    a part gives of itself is the kernel's own, which can stop short of a
    free-form face: a sphere about that one alone left the tip of a long
    free-form solid outside it, and a part passing through the tip was pruned
    as never in the way.

    The centre stays where it was because the headings leading away from a
    part are drawn from it, and the middle of a box widened by control points
    can sit well off the part.
    """
    box = part.bounding_box()
    centre, size = box.center(), box.size
    middle = (float(centre.X), float(centre.Y), float(centre.Z))
    if reach is None:
        return middle, float(0.5 * (size.X**2 + size.Y**2 + size.Z**2) ** 0.5)
    corners = reach.Get()
    farthest = [
        max(abs(low - at), abs(high - at))
        for at, low, high in zip(middle, corners[:3], corners[3:], strict=True)
    ]
    return middle, sum(side**2 for side in farthest) ** 0.5


def _look_ahead(parts) -> float | None:
    """How far past a contact the walk looks for a clash, from the thinnest part.

    ``None`` for a part with no measurable size, which stops the search.
    """
    thinnest = None
    for part in parts:
        size = part.bounding_box().size
        # Read as a box rather than compared: the smallest of a NaN and two
        # numbers depends on their order, and a NaN look-ahead let the search
        # run on parts whose sweeps the kernel never came back from.
        sides = _dimensions((float(size.X), float(size.Y), float(size.Z)))
        if sides is None:
            return None
        smallest = min(sides)
        if thinnest is None or smallest < thinnest:
            thinnest = smallest
    if thinnest is None or thinnest <= 0.0:
        return None
    return thinnest * _LOOK_AHEAD_FRACTION


class _BudgetSpent(Exception):
    """Raised when the measurement has used all the kernel work it was allowed."""


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

    def check(self) -> None:
        """Raise once the clock has run out, spending nothing.

        For work that costs time and no probe: a pair settled by its boxes is
        charged nothing, and a model of very many parts has very many of those.
        """
        if time.monotonic() >= self._deadline:
            raise _BudgetSpent
