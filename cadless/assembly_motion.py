"""The motion an assembly is played from, and what makes a file one.

A build checked as an assembly writes its motion beside its exports, and a
catalog item carries the one whoever baked it wrote. Either way the viewport
plays a motion only if it describes the parts on screen, and a motion it
refuses shows as nothing failing at all -- only a model with no player. So the
rules it plays by are stated here, once, for whatever writes a motion and
whatever takes one in, and a file is refused where it is made or received
rather than found unplayable where it is shown.

The file is one JSON object. A part is named by its ordinal, and every list
indexed by part has one entry per drawn part:

- ``order``: the ordinals, in the order the parts go together, each once.
- ``releases``: per part, ``[]`` for a part that stays where it is, or the
  heading it comes off along -- three numbers in the build's frame, millimetres
  and Z up. At least one part has a heading, or nothing would move.
- ``stretch``: per part, how many spacings it goes out when no travel is given,
  a whole number from 1 to the part count.
- ``explode_fraction``: that spacing, as a fraction of the assembled model's
  largest dimension.
- ``travel``, optional: per part, how far it goes out, as a fraction of the
  same dimension; more than none for every part with a heading.
- ``steps``, optional: ``order`` cut into runs, in order; the parts of one run
  move at the same time. Absent, every part is a run of its own.
- ``subassemblies``, optional and only beside ``steps``: groups of two or more
  parts inside one run, which come off whole -- along the heading their members
  share in ``releases``, or not at all when none of them has one -- and then
  come apart, each member along its slot in ``splits`` by its ``split_travel``.

A key nothing here reads is ignored, at the top and in a sub-assembly alike, so
a file can carry what a later viewer plays without an earlier one refusing it.

The standard library only: what checks a received item has to stay cheap to
import, and nothing here needs more than arithmetic.
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence

#: The artifact kind a version's motion is filed under, and the key a catalog
#: step lists its motion by.
MOTION_KIND = "assembly"

#: The file a motion is written to beside a build's exports, and the name a
#: version's copy of one goes by.
MOTION_FILE = "assembly.json"

#: The most bytes a motion file may take. Far more than a motion needs, and a
#: file past it is refused before any of it is decoded: it arrives with items
#: from anywhere, and a reader that parses whatever it is handed can be kept
#: parsing.
MAX_MOTION_BYTES = 1024 * 1024

#: Below this a heading is not a direction. A zero-length vector cannot be
#: normalised, and scaling it would move the part nowhere while claiming to move
#: it somewhere.
HEADING_EPSILON = 1e-9

#: The widest spacing accepted, as a fraction of the assembled model's largest
#: dimension: the ``explode_fraction`` a motion is spaced by, and the spacing in
#: the cap on a travel. Wider than the model itself and the parts leave the frame
#: rather than show a joint.
MAX_EXPLODE_FRACTION = 1

#: The furthest one part may travel, per part in the assembly, as a fraction of
#: the assembled model's largest dimension. Along any heading the model spans at
#: most its diagonal, √3 of that dimension, so each part placed before another
#: adds at most that and a spacing to how far the next has to go: √3 and the
#: widest spacing, rounded down. A part never has more parts before it than one
#: fewer than the count, which is what keeps the rounding safe. Anything beyond
#: is a file that is wrong rather than a part that is far.
MAX_TRAVEL_PER_PART = 2.732

#: How close two unit headings have to be to count as one direction: their dot
#: product at least one less this. Wide enough for float noise, far too narrow
#: for an angle anyone could see.
SAME_HEADING_TOLERANCE = 1e-6

# What JSON's two containers arrive as. A tuple is an array too: it is written as
# one, so a motion built in Python is judged as the file it becomes.
_ARRAY = (list, tuple)


def read_motion(raw: bytes, parts: Sequence[int]) -> dict:
    """The motion in ``raw``, read as a motion of ``parts``, or a ``ValueError``
    saying why it is not one.

    Refused, in this order: more than :data:`MAX_MOTION_BYTES`, before a byte of
    it is decoded; bytes that are not UTF-8 JSON; the ``NaN``, ``Infinity`` and
    ``-Infinity`` that Python reads and JSON has no spelling for, wherever they
    are; nesting too deep to read; and anything :func:`motion_problems` finds
    wrong, every problem named.
    """
    if len(raw) > MAX_MOTION_BYTES:
        raise ValueError(
            f"the motion is {len(raw)} bytes, more than the {MAX_MOTION_BYTES} a motion may take"
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"the motion is not UTF-8 text: {exc.reason} at byte {exc.start}") from exc
    try:
        payload = json.loads(text, parse_constant=_refuse_constant)
    except RecursionError as exc:
        raise ValueError("the motion nests too deeply to be read") from exc
    except ValueError as exc:
        raise ValueError(f"the motion is not JSON: {exc}") from exc
    problems = motion_problems(payload, parts)
    if problems:
        raise ValueError("the motion would not play: " + "; ".join(problems))
    return payload


def motion_problems(payload: object, parts: Sequence[int]) -> list[str]:
    """Every rule ``payload`` breaks as a motion of ``parts``, one message each.

    ``parts`` are the ordinals of the parts drawn. An empty list is a motion the
    viewport plays, and anything in it is a reason it would not, so the rules are
    the viewport's, no more and no fewer: a payload refused here that the
    viewport plays is an item turned away for nothing, and one passed here that
    the viewport refuses is a player that never appears.

    Never raises, whatever ``payload`` is -- any JSON value, nested any way --
    and never walks into a value no rule reads, nor prints one: a message names
    the first entry that breaks its rule by where it sits. A rule that can only
    be judged on fields another rule has already found wrong is left to that
    one -- whether the runs add up to an order that is not a list of ordinals,
    say -- rather than reported as well.
    """
    if not isinstance(payload, dict):
        return ["the motion is not a JSON object"]
    found: dict[str, str] = {}
    count = len(parts)
    order = _check_order(payload, sorted(parts), found)
    headings = _check_releases(payload, count, order, found)
    _check_stretch(payload, count, found)
    _check_explode_fraction(payload, found)
    _check_travel(payload, count, headings, found)
    runs = _check_steps(payload, order, found)
    _check_subassemblies(payload, count, runs, headings, found)
    return list(found.values())


def _check_order(payload: dict, drawn: list[int], found: dict[str, str]) -> list | None:
    """The order, if it is a list of whole numbers as long as the parts drawn.

    Counted before it is walked, so an order of any length is refused without
    reading, copying or sorting it.
    """
    order = payload.get("order")
    if not isinstance(order, _ARRAY):
        found["order"] = "order is missing or is not a list"
        return None
    if len(order) != len(drawn):
        found["order"] = f"order lists {len(order)} parts, but {len(drawn)} are drawn"
        return None
    for position, ordinal in enumerate(order):
        if not _is_whole(ordinal):
            found["order entries"] = f"order entry {position} is not a whole number"
            return None
    if sorted(order) != drawn:
        found["order names"] = "order does not name each drawn part exactly once"
    return list(order)


def _check_releases(
    payload: dict, count: int, order: list | None, found: dict[str, str]
) -> list | None:
    """Each part's unit heading, or ``None`` for a part that stays -- if every
    slot is well formed. Slots are indexed by ordinal, so every ordinal the
    order names needs one."""
    releases = payload.get("releases")
    if not isinstance(releases, _ARRAY):
        found["releases"] = "releases is missing or is not a list"
        return None
    if len(releases) != count:
        found["releases"] = f"releases has {len(releases)} slots, but {count} parts are drawn"
        return None
    if order is not None and not all(0 <= ordinal < count for ordinal in order):
        found["slot for every ordinal"] = "order names a part releases has no slot for"
    headings = []
    for position, slot in enumerate(releases):
        if not _is_slot(slot):
            found["release slots"] = (
                f"releases slot {position} is neither [] nor three finite numbers"
            )
            return None
        headings.append(_unit(slot))
    if all(heading is None for heading in headings):
        found["something moves"] = "no part has a heading in releases, so nothing would move"
    return headings


def _check_stretch(payload: dict, count: int, found: dict[str, str]) -> None:
    """A part never needs more spacings than there are parts for it to clear."""
    stretch = payload.get("stretch")
    if not isinstance(stretch, _ARRAY):
        found["stretch"] = "stretch is missing or is not a list"
        return
    if len(stretch) != count:
        found["stretch"] = f"stretch has {len(stretch)} entries, but {count} parts are drawn"
        return
    for position, spacings in enumerate(stretch):
        if not (_is_whole(spacings) and 1 <= spacings <= count):
            found["stretch entries"] = (
                f"stretch entry {position} is not a whole number from 1 to {count}"
            )
            return


def _check_explode_fraction(payload: dict, found: dict[str, str]) -> None:
    fraction = _finite(payload.get("explode_fraction"))
    if fraction is None:
        found["explode_fraction"] = "explode_fraction is missing or is not a finite number"
    elif not 0 < fraction <= MAX_EXPLODE_FRACTION:
        found["explode_fraction range"] = (
            f"explode_fraction is not above 0 and at most {MAX_EXPLODE_FRACTION}"
        )


def _check_travel(payload: dict, count: int, headings: list | None, found: dict[str, str]) -> None:
    """Optional, so only an absent key goes without: one present and holding
    null is a travel that is wrong, not one left out. A part with a heading and
    no travel would make its joint play as nothing."""
    if "travel" not in payload:
        return
    travel = payload["travel"]
    if not isinstance(travel, _ARRAY):
        found["travel"] = "travel is given but is not a list"
        return
    if len(travel) != count:
        found["travel"] = f"travel has {len(travel)} entries, but {count} parts are drawn"
        return
    furthest = count * MAX_TRAVEL_PER_PART
    distances = [_finite(distance) for distance in travel]
    for position, distance in enumerate(distances):
        if distance is None or not 0 <= distance <= furthest:
            found["travel entries"] = (
                f"travel entry {position} is not a finite number from 0 to {furthest:g}"
            )
            return
    if headings is None:
        return
    for part, heading in enumerate(headings):
        if heading is not None and distances[part] == 0:
            found["travel for every heading"] = (
                f"travel entry {part} is 0, but part {part} has a heading to come off along"
            )
            return


def _check_steps(payload: dict, order: list | None, found: dict[str, str]) -> list | None:
    """The runs, if ``steps`` is given and each run is a list of whole numbers."""
    if "steps" not in payload:
        return None
    steps = payload["steps"]
    if not isinstance(steps, _ARRAY):
        found["steps"] = "steps is given but is not a list"
        return None
    for position, run in enumerate(steps):
        if not (isinstance(run, _ARRAY) and run and all(_is_whole(ordinal) for ordinal in run)):
            found["runs"] = f"steps run {position} is not a non-empty list of whole numbers"
            return None
    if order is not None and [ordinal for run in steps for ordinal in run] != order:
        found["runs add up"] = "steps do not add up to the order, run after run"
    return [list(run) for run in steps]


def _check_subassemblies(
    payload: dict,
    count: int,
    runs: list | None,
    headings: list | None,
    found: dict[str, str],
) -> None:
    if "subassemblies" not in payload:
        return
    if "steps" not in payload:
        found["beside steps"] = "subassemblies are given without steps"
    entries = payload["subassemblies"]
    if not isinstance(entries, _ARRAY):
        found["subassemblies"] = "subassemblies is given but is not a list"
        return
    run_of: dict = {}
    for index, run in enumerate(runs or ()):
        for ordinal in run:
            run_of.setdefault(ordinal, index)
    furthest = count * MAX_TRAVEL_PER_PART
    taken: set = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            found.setdefault("entries", f"subassembly {index} is not an object")
            continue
        members = _members(entry.get("parts"))
        if members is None:
            found.setdefault(
                "members",
                f"subassembly {index}'s parts are not two or more different whole numbers",
            )
        else:
            homes = {run_of.get(member) for member in members}
            if runs is not None and (len(homes) != 1 or None in homes):
                found.setdefault(
                    "one run", f"subassembly {index}'s parts are not all in one run of steps"
                )
            if taken & set(members):
                found.setdefault("apart", f"subassembly {index} shares a part with an earlier one")
            taken |= set(members)
            _check_members_leave_as_one(index, members, headings, found)
        splits = _check_splits(index, entry.get("splits"), members, found)
        distances = _check_split_travel(index, entry.get("split_travel"), members, furthest, found)
        if splits is not None and distances is not None and len(splits) == len(distances):
            _check_split_travel_moves_what_splits(index, splits, distances, found)


def _members(parts: object) -> list | None:
    """A sub-assembly's members, if they are two or more different whole numbers."""
    if not isinstance(parts, _ARRAY) or len(parts) < 2:
        return None
    if not all(_is_whole(member) for member in parts) or len(set(parts)) != len(parts):
        return None
    return list(parts)


def _check_members_leave_as_one(
    index: int, members: list, headings: list | None, found: dict[str, str]
) -> None:
    """A sub-assembly comes off whole, so its members' own headings agree: none
    of them has one, or all of them point the same way."""
    if headings is None or not all(0 <= member < len(headings) for member in members):
        return
    own = [headings[int(member)] for member in members]
    if all(heading is None for heading in own):
        return
    first = own[0]
    if first is None or any(
        heading is None or _dot(heading, first) < 1 - SAME_HEADING_TOLERANCE for heading in own
    ):
        found.setdefault(
            "one heading", f"subassembly {index}'s parts do not all come off along one heading"
        )


def _check_splits(
    index: int, splits: object, members: list | None, found: dict[str, str]
) -> list | None:
    """Each member's unit heading off the sub-assembly, or ``None`` for one that
    stays with it -- if the slots are well formed."""
    if not isinstance(splits, _ARRAY) or (members is not None and len(splits) != len(members)):
        found.setdefault(
            "splits",
            f"subassembly {index}'s splits are not a list with a slot for each of its parts",
        )
        return None
    for position, slot in enumerate(splits):
        if not _is_slot(slot):
            found.setdefault(
                "split slots",
                f"subassembly {index}'s split slot {position} is neither [] "
                "nor three finite numbers",
            )
            return None
    return [_unit(slot) for slot in splits]


def _check_split_travel(
    index: int, split_travel: object, members: list | None, furthest: float, found: dict[str, str]
) -> list | None:
    if not isinstance(split_travel, _ARRAY) or (
        members is not None and len(split_travel) != len(members)
    ):
        found.setdefault(
            "split_travel",
            f"subassembly {index}'s split_travel is not a list with an entry for each of its parts",
        )
        return None
    distances = [_finite(distance) for distance in split_travel]
    for position, distance in enumerate(distances):
        if distance is None or not 0 <= distance <= furthest:
            found.setdefault(
                "split_travel entries",
                f"subassembly {index}'s split_travel entry {position} is not a finite number "
                f"from 0 to {furthest:g}",
            )
            return None
    return distances


def _check_split_travel_moves_what_splits(
    index: int, splits: list, distances: list, found: dict[str, str]
) -> None:
    """A member that splits off goes some way, and one that stays goes none:
    a split with no travel plays as nothing, and travel with no split heading
    has no direction to go in."""
    for position, (heading, distance) in enumerate(zip(splits, distances, strict=True)):
        if heading is not None and distance == 0:
            found.setdefault(
                "split moves",
                f"subassembly {index}'s split_travel entry {position} is 0, "
                "but that member splits off along a heading",
            )
        if heading is None and distance != 0:
            found.setdefault(
                "stay stays",
                f"subassembly {index}'s split_travel entry {position} is above 0, "
                "but that member has no split heading",
            )


def _is_slot(slot: object) -> bool:
    """Whether a heading slot is ``[]`` or three finite numbers."""
    if not isinstance(slot, _ARRAY):
        return False
    return len(slot) == 0 or (len(slot) == 3 and all(_finite(c) is not None for c in slot))


def _unit(slot: Sequence[float]) -> tuple[float, float, float] | None:
    """The unit direction a well-formed slot names, or ``None`` for none.

    Measured after dividing by its largest component, so a heading of numbers
    too large to square still has a length and a direction, rather than an
    infinite length that would leave it pointing nowhere. A square root of the
    squares summed x, y, z rather than :func:`math.hypot`, whose last bit each
    runtime rounds its own way: every step here is one IEEE arithmetic rounds
    exactly alike everywhere, so a reader of the same file written in another
    language from the same steps reaches the same bit, at the thresholds as
    anywhere else.
    """
    if len(slot) != 3:
        return None
    x, y, z = (float(c) for c in slot)
    scale = max(abs(x), abs(y), abs(z))
    if scale == 0:
        return None
    a, b, c = x / scale, y / scale, z / scale
    norm = math.sqrt(a * a + b * b + c * c)
    if scale * norm <= HEADING_EPSILON:
        return None
    return (a / norm, b / norm, c / norm)


def _dot(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _finite(value: object) -> float | None:
    """``value`` as a float, if it is a finite number; ``None`` otherwise.

    A boolean is not a number here, though Python counts it as one: JSON has
    ``true``, and a viewer reading it gets no number. An integer too large for
    a float is not finite, as the same digits read by a viewer are not.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None


def _is_whole(value: object) -> bool:
    """Whether ``value`` is a whole number, read as the viewport reads one: an
    integer, or a float with nothing after the point -- never a boolean."""
    number = _finite(value)
    return number is not None and number.is_integer()


def _refuse_constant(name: str) -> None:
    raise ValueError(f"{name} is not a number JSON can carry")
