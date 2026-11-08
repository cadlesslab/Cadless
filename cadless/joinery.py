"""Cut a part at a plane and join the halves with a joint made by construction.

A part too large for the printer's bed is split into pieces that each fit, and
every seam needs a joint that holds the pieces together and comes apart only the
way the piece goes in. Written by hand, those joints failed in the same few ways:
buried in a pocket closed at both ends, swept across the way the piece goes in,
placed over a hole, built from an extrude read as centred. :func:`split_and_join`
builds the joint from the cut itself instead, so it runs along the axis the piece
goes in, sits only where both cut faces are solid, and fits with the clearance
the printer holds.

Scripts are handed :func:`split_and_join` in the globals :func:`script_namespace`
gives them rather than importing this module, so the import allow-list stays as
it is. That is no wall around the module: whatever it holds, a script can reach,
so it imports nothing beyond build123d, the standard library and the printer
profile's limits.
"""

from __future__ import annotations

import math
from typing import NamedTuple

from build123d import (
    Align,
    Box,
    Cylinder,
    Edge,
    GeomType,
    Keep,
    Plane,
    Polygon,
    Pos,
    Solid,
    Vector,
    Vertex,
    extrude,
)

from cadless.printer_profile import PRINTER_PROFILE_LIMITS

#: Off square, each flank of a dovetail: enough to lock the halves across the
#: seam. Swept along an upright axis, a dovetail prints without support; along a
#: level one, how its flanks print depends on how the part lies on the bed.
_FLANK_DEGREES = 15.0
#: A dovetail's root, across the seam, at most and at least (mm). Below the least
#: it is too weak to hold the halves together.
_ROOT_MOST, _ROOT_LEAST = 20.0, 3.0
#: How deep a dovetail reaches into the other half, per unit of its root.
_DEPTH_PER_ROOT = 0.6
#: A peg's diameter, at most and at least (mm).
_PEG_MOST, _PEG_LEAST = 8.0, 3.0
#: Where a peg's flat side stands from its axis, as a share of its radius: enough
#: to stop the halves turning about the peg, little enough to keep most of it.
_FLAT = 0.5
#: The shortest a joint may run along the axis the half goes in (mm).
_RUN_LEAST = 3.0
#: Joints per seam, at most: a second, as far from the first as the face allows,
#: shares the load and holds the seam closed at both ends.
_JOINTS_MOST = 2
#: The clearance a printer can hold, in mm: the range a printer profile accepts.
_CLEARANCE_LEAST, _CLEARANCE_MOST = PRINTER_PROFILE_LIMITS["printer_joint_clearance"]
#: Positions sampled across a cut face when looking for room for a joint.
_SAMPLES = 240
#: How far apart, in mm, two sampled runs' ends may be and still be the same run.
_SAME_RUN = 1e-3
#: Steps along each side of a cut face when looking for room for a peg.
_GRID = 24
#: Places tried along one band of the cut face, at most, half a joint's reach
#: apart: enough to find room between features near either end without trying
#: every sample.
_POSITIONS_MOST = 9
#: What a dovetail's root is multiplied by each time the half cannot hold it.
_SHRINK = 0.75
#: How far, in mm, a cut or a sampling line is carried past what it crosses --
#: behind the seam, past the half's ends -- so it never stops on a face.
_OVERCUT = 1.0
#: How far past the run it stands on, in mm, a socket may cut through the part as
#: its own opening: enough to come out through a surface a few degrees off square,
#: too little to groove a feature standing in its way, whatever the clearance.
_EXIT = 1.0
#: The share of a volume a boolean may miss or add without it counting.
_VOLUME_SHARE = 1e-6
_EPS = 1e-6


class JoineryError(ValueError):
    """A part could not be split and joined as asked; the message says what to change."""


def script_namespace() -> dict[str, object]:
    """The globals a generated script starts with: the helpers it is handed.

    A new dict on every call, so what one script does to its namespace never
    reaches the next script run in the same process.
    """
    return {"split_and_join": split_and_join}


def split_and_join(
    part,
    plane: Plane,
    insert_along,
    clearance: float,
    *,
    names: tuple[str, str] | None = None,
) -> tuple[Solid, Solid]:
    """Cut ``part`` at ``plane`` into two solids joined by construction.

    ``insert_along`` is the axis the second half is put on along -- one of the six
    axis directions, the same one every other fit holding it uses. Lying in the
    plane, it gives dovetails swept along it right through the cut face, so their
    sockets are open at both ends; along the plane's normal, it gives pegs, each
    round with one flat side.
    Each joint sits where the cut face is solid along its whole run, its socket
    keeping a margin of ``max(1, 2 * clearance)`` mm from every edge and hole, and
    the socket is the tab grown by ``clearance`` on every side that meets it.

    Returns ``(behind, ahead)``: the half behind the plane's normal, which carries
    the tabs or pegs, and the half the normal points into, which carries the
    sockets. They are labelled from ``names``, or from the part's own label.

    Raises :class:`JoineryError`, saying what to change, when

    - ``part`` is not one solid, or ``plane`` is not a Plane or misses the part;
    - ``clearance`` is outside what a printer holds;
    - ``insert_along`` is not one of the six axis directions, or neither lies in
      the plane nor runs along its normal;
    - a side of the cut comes out in several pieces;
    - no place on the cut face holds a joint, or every socket that would fit
      would cut through the part beyond the cut face;
    - a joint would leave a half in several pieces.

    A failure inside the geometry kernel is not caught: it reaches the script as
    the kernel raised it.
    """
    _check_clearance(clearance)
    if not isinstance(plane, Plane):
        raise JoineryError(
            "plane must be a Plane, such as Plane(origin=(x, 0, 0), z_dir=(1, 0, 0)) for a "
            f"cut at that x: got {plane!r}"
        )
    axis = _axis(insert_along)
    normal = Vector(plane.z_dir).normalized()
    where = _describe(plane)
    across = abs(axis.dot(normal))
    if _EPS < across < 1 - _EPS:
        raise JoineryError(
            f"{where}: insert_along {_name(axis)} neither lies in the plane nor runs along "
            "its normal; cut on a plane that contains the axis the part goes in along "
            "(for dovetails) or faces it (for pegs)"
        )
    behind, ahead = _halves(_one_solid(part), plane, where)
    section = _section(behind, ahead, plane)
    margin = max(1.0, 2 * clearance)
    blocked = False
    if across <= _EPS:
        joints, blocked = _dovetails(section, plane, axis, ahead, clearance, margin)
    else:
        joints = _pegs(section, plane, ahead, clearance, margin)
    if not joints and blocked:
        raise JoineryError(
            f"{where}: wherever a joint fits, its socket along {_name(axis)} would cut a "
            f"groove through the part beyond the cut face, more than {_EXIT:g} mm past "
            "it; insert along another axis, or move the seam"
        )
    if not joints:
        least = _ROOT_LEAST if across <= _EPS else _PEG_LEAST
        raise JoineryError(
            f"{where}: no place on the cut face is solid enough for a joint {least:g} mm "
            f"across with {margin:g} mm to spare along {_name(axis)}; make the wall thicker "
            "there, move the seam, or insert along another axis"
        )
    for tab, socket in joints:
        behind = _single(behind.fuse(tab), where)
        ahead = _single(ahead.cut(socket), where)
    first, second = names or (f"{part.label or 'part'} 1", f"{part.label or 'part'} 2")
    behind.label, ahead.label = first, second
    return behind, ahead


def _check_clearance(clearance) -> None:
    # A bool is an int to isinstance, and True would pass as a millimetre.
    number = isinstance(clearance, int | float) and not isinstance(clearance, bool)
    if not number or not _CLEARANCE_LEAST <= clearance <= _CLEARANCE_MOST:
        raise JoineryError(
            f"clearance must be a number of millimetres from {_CLEARANCE_LEAST:g} to "
            f"{_CLEARANCE_MOST:g}, the range a printer holds: got {clearance!r}"
        )


def _axis(insert_along) -> Vector:
    """``insert_along`` as a unit vector, refused unless it is one of the six axes."""
    direction = getattr(insert_along, "direction", insert_along)
    try:
        vector = Vector(direction)
    except (TypeError, ValueError):
        vector = None
    if vector is not None and vector.length > _EPS:
        vector = vector.normalized()
        if max(abs(vector.X), abs(vector.Y), abs(vector.Z)) > 1 - _EPS:
            return Vector(round(vector.X), round(vector.Y), round(vector.Z))
    raise JoineryError(
        "insert_along must be one of the six axis directions -- Axis.X, Axis.Y, Axis.Z "
        f"or one of them reversed: got {insert_along!r}"
    )


def _name(axis: Vector) -> str:
    sign = "-" if axis.X + axis.Y + axis.Z < 0 else "+"
    letter = "X" if abs(axis.X) > 0.5 else "Y" if abs(axis.Y) > 0.5 else "Z"
    return sign + letter


def _describe(plane: Plane) -> str:
    def number(value: float) -> str:
        return f"{round(value, 2) + 0.0:g}"

    origin, normal = Vector(plane.origin), Vector(plane.z_dir).normalized()
    at = ", ".join(number(v) for v in (origin.X, origin.Y, origin.Z))
    facing = ", ".join(number(v) for v in (normal.X, normal.Y, normal.Z))
    return f"the plane through ({at}) facing ({facing})"


def _one_solid(part) -> Solid:
    if not hasattr(part, "solids"):
        raise JoineryError(
            f"part must be a build123d shape, such as a Box or a Part: got {type(part).__name__}"
        )
    solids = part.solids()
    if len(solids) != 1:
        raise JoineryError(f"split_and_join cuts one solid at a time: got {len(solids)}")
    return solids[0]


def _halves(solid: Solid, plane: Plane, where: str) -> tuple[Solid, Solid]:
    """The solid's pieces behind and ahead of ``plane``, one each or refused."""
    behind, ahead = [], []
    for found in _solids_in(solid.split(plane, keep=Keep.BOTH)):
        side = plane.to_local_coords(found.center()).Z
        (ahead if side > 0 else behind).append(found)
    if not behind or not ahead:
        raise JoineryError(f"{where} does not cut the part: move it to where the part is")
    for side in (behind, ahead):
        if len(side) > 1:
            raise JoineryError(
                f"{where} leaves one side in {len(side)} pieces; move the plane so each "
                "side stays one piece, or split the pieces apart first"
            )
    return behind[0], ahead[0]


def _solids_in(found):
    """Every solid in what a split returns: a shape, a list of them, or nothing."""
    if found is None:
        return
    if isinstance(found, list | tuple):
        for item in found:
            yield from _solids_in(item)
    else:
        yield from found.solids()


def _volume(shape) -> float:
    """The volume of a boolean's result, which is None where the shapes never meet."""
    return 0.0 if shape is None else shape.volume


def _section(behind: Solid, ahead: Solid, plane: Plane) -> list:
    """The cut face: where the halves meet in ``plane``, the only place a joint can stand.

    Taken from both halves rather than one, because a face of the part that only
    lies flush with the plane has material on one side of it alone, and a joint
    standing there would stand in the air.
    """
    common = []
    for face in _in_plane(behind, plane):
        for other in _in_plane(ahead, plane):
            shared = face & other  # None, an edge or a face, as the two meet
            common.extend(
                found
                for found in (shared.faces() if shared is not None else [])
                if found.area > _EPS
            )
    return common


def _in_plane(solid: Solid, plane: Plane) -> list:
    """The solid's flat faces lying in ``plane``."""
    normal = Vector(plane.z_dir).normalized()
    return [
        face
        for face in solid.faces()
        if face.geom_type == GeomType.PLANE
        and abs(abs(face.normal_at().dot(normal)) - 1) < _EPS
        and abs(plane.to_local_coords(face.center()).Z) < _EPS
    ]


def _single(shape, where: str) -> Solid:
    solids = shape.solids()
    if len(solids) != 1:
        raise JoineryError(
            f"{where}: the joint would leave a half in {len(solids)} pieces; move the seam "
            "to where the cut face is thicker"
        )
    return solids[0]


# --- dovetails: the axis lies in the plane --------------------------------------


class _Site(NamedTuple):
    """A place a dovetail may stand: its centre across the seam, how far its socket
    and margin reach to either side of it at full size, that size, and the run
    along the axis it is swept over."""

    centre: float
    half: float
    root: float
    low: float
    high: float


def _dovetails(section, plane, axis, ahead, clearance, margin):
    """Tabs and sockets for up to two dovetails swept along ``axis``, and whether a
    place was given up because its socket would cut the part beyond the cut face.

    Positions across the cut face are sampled; where the line along ``axis``
    crosses the face in one run, unbroken by a hole, and the same run at
    neighbouring positions, the face is a band a dovetail can stand in. Places
    are spread along each band rather than kept to its ends, so a band whose
    ends the half cannot hold still takes a joint in between.
    """
    frame = Plane(origin=plane.origin, x_dir=axis, z_dir=Vector(plane.z_dir).normalized())
    sites = []
    for low, high, start, end in _bands(section, frame):
        root = _widest_root(end - start, clearance, margin)
        if root < _ROOT_LEAST:
            continue
        half = _reach(root, clearance + margin)
        for centre in _positions(start + half, end - half, half / 2):
            sites.append(_Site(centre, half, root, low, high))
    extent = _extent(ahead, frame)
    blocked = []

    def make(site, shrunk):
        joint, in_the_way = _dovetail(frame, ahead, site, extent, clearance, margin, shrunk)
        blocked.append(in_the_way)
        return joint

    return _choose(sites, make), any(blocked)


def _widest_root(width: float, clearance: float, margin: float) -> float:
    """The widest dovetail root whose socket, grown by the margin, fits ``width``."""
    root = (width - 2 * _reach(0.0, clearance + margin)) / (2 * _reach(1.0, 0.0))
    return min(_ROOT_MOST, root)


def _reach(root: float, grow: float) -> float:
    """How far to either side of its centre a dovetail ``root`` wide, grown by
    ``grow`` on its flanks and tip, reaches across the seam at its widest."""
    slope = math.tan(math.radians(_FLANK_DEGREES))
    depth = root * _DEPTH_PER_ROOT + grow
    return root / 2 + grow / math.cos(math.radians(_FLANK_DEGREES)) + depth * slope


def _bands(section, frame) -> list[tuple[float, float, float, float]]:
    """Runs of the cut face along the frame's x, as ``(low, high, start, end)``.

    ``low``-``high`` is the run along the axis, the same at every sampled
    position from ``start`` to ``end`` across it. Only the sampled positions are
    looked at, so a hole narrower than the step between them can lie inside a
    band: the room check still finds one that reaches into the half ahead, and a
    tab over one that does not bridges it.
    """
    if not section:
        return []
    corners = [frame.to_local_coords(face).bounding_box() for face in section]
    x0, x1 = min(c.min.X for c in corners), max(c.max.X for c in corners)
    y0, y1 = min(c.min.Y for c in corners), max(c.max.Y for c in corners)
    step = (y1 - y0) / _SAMPLES
    bands, current = [], None
    for k in range(_SAMPLES + 1):
        across = y0 + k * step
        runs = _runs(section, frame, across, x0 - _OVERCUT, x1 + _OVERCUT)
        run = runs[0] if len(runs) == 1 and runs[0][1] - runs[0][0] >= _RUN_LEAST else None
        same = (
            current is not None
            and run is not None
            and abs(run[0] - current[0]) < _SAME_RUN
            and abs(run[1] - current[1]) < _SAME_RUN
        )
        if same:
            current[3] = across
        else:
            if current is not None:
                bands.append(tuple(current))
            current = [run[0], run[1], across, across] if run is not None else None
    if current is not None:
        bands.append(tuple(current))
    return [band for band in bands if band[3] > band[2]]


def _runs(section, frame, across, start, end) -> list[tuple[float, float]]:
    """Where the line at ``across``, along the frame's x, lies on the cut face."""
    line = Edge.make_line(
        frame.from_local_coords(Vector(start, across, 0)),
        frame.from_local_coords(Vector(end, across, 0)),
    )
    runs = []
    for face in section:
        hit = face & line
        for edge in hit.edges() if hit is not None else []:
            ends = sorted(frame.to_local_coords(Vector(v)).X for v in edge.vertices())
            if ends[-1] - ends[0] > _EPS:
                runs.append((ends[0], ends[-1]))
    runs.sort()
    merged: list[tuple[float, float]] = []
    for low, high in runs:
        if merged and low <= merged[-1][1] + _EPS:
            merged[-1] = (merged[-1][0], max(high, merged[-1][1]))
        else:
            merged.append((low, high))
    return merged


def _positions(first: float, last: float, step: float) -> list[float]:
    """Centres from ``first`` to ``last``, both ends included, ``step`` or less apart
    until there are as many as a band is given."""
    if last - first <= _EPS:
        return [first]
    count = min(_POSITIONS_MOST - 1, math.ceil((last - first) / step))
    return [first + (last - first) * k / count for k in range(count + 1)]


def _choose(sites, make) -> list:
    """Up to two joints: the first as far out on the face as one can be made, the
    second as far from it as one can be made without their sockets and margins
    meeting -- each full size wherever some place takes one, and smaller only
    where none does.

    ``make(site, shrunk)`` builds a site's joint at full size, or ``shrunk`` below
    it, or returns None where the half cannot hold one; it is called only for the
    sites tried, and at most once for each site and size.
    """
    if not sites:
        return []
    made: dict = {}

    def first_made(candidates):
        for shrunk in (False, True):
            for site in candidates:
                if (site, shrunk) not in made:
                    made[site, shrunk] = make(site, shrunk)
                if made[site, shrunk] is not None:
                    return site, made[site, shrunk]
        return None

    middle = (min(site.centre for site in sites) + max(site.centre for site in sites)) / 2
    found = first_made(sorted(sites, key=lambda site: -abs(site.centre - middle)))
    if found is None:
        return []
    first, joint = found
    apart = [s for s in sites if abs(s.centre - first.centre) >= first.half + s.half]
    second = first_made(sorted(apart, key=lambda s: -abs(s.centre - first.centre)))
    return [joint] if second is None else [joint, second[1]]


def _extent(ahead, frame) -> tuple[float, float]:
    box = frame.to_local_coords(ahead).bounding_box()
    return box.min.X - _OVERCUT, box.max.X + _OVERCUT


def _dovetail(frame, ahead, site, extent, clearance, margin, shrunk):
    """A tab and its socket at ``site`` -- full size, or ``shrunk`` smaller and
    smaller below it -- or None; and whether a size the half held was given up
    for the socket's path.

    The socket runs right through the half along the axis, open at both ends so
    the tab can slide in from either. Just past the run the tab stands on, it
    comes out through the part's surface and cuts its own opening there; further
    on, nothing of the half may lie in its way, or it would cut a groove.
    """
    roots = [site.root]
    if shrunk:
        roots = []
        root = site.root * _SHRINK
        while root >= _ROOT_LEAST:
            roots.append(root)
            root *= _SHRINK
    blocked = False
    for root in roots:
        depth = root * _DEPTH_PER_ROOT
        outline = _trapezoid(site.centre, root, depth, clearance, _OVERCUT)
        grown = _trapezoid(site.centre, root, depth, clearance + margin, 0.0)
        if not _fills(ahead, _prism(frame, grown, site.low, site.high)):
            continue
        beyond = ((extent[0], site.low - _EXIT), (site.high + _EXIT, extent[1]))
        if any(_meets(ahead, _prism(frame, outline, *ends)) for ends in beyond if _apart(*ends)):
            blocked = True
            continue
        tab = _prism(frame, _trapezoid(site.centre, root, depth, 0.0, 0.0), site.low, site.high)
        return (tab, _prism(frame, outline, *extent)), blocked
    return None, blocked


def _apart(low: float, high: float) -> bool:
    """Whether a stretch from ``low`` to ``high`` has any length to build on."""
    return high - low > _EPS


def _fills(half, room) -> bool:
    """Whether ``room`` lies wholly inside ``half``'s material."""
    return _volume(half & room) >= room.volume * (1 - _VOLUME_SHARE)


def _meets(half, room) -> bool:
    """Whether any of ``half``'s material lies inside ``room``."""
    return _volume(half & room) > room.volume * _VOLUME_SHARE


def _trapezoid(centre: float, root: float, depth: float, grow: float, below: float):
    """A dovetail's outline across the seam: ``(across, into the half ahead)`` corners.

    Grown by ``grow`` on the flanks and the tip, and reaching ``below`` behind the
    seam so a socket cuts cleanly through the face it starts on. Each flank is one
    straight line, ``below`` the seam as much as past it, so a socket's flanks run
    parallel to its tab's and the gap between them is the same at every depth.
    """
    slope = math.tan(math.radians(_FLANK_DEGREES))
    side = grow / math.cos(math.radians(_FLANK_DEGREES))
    bottom, top = -below, depth + grow
    return [
        (centre - root / 2 - side - bottom * slope, bottom),
        (centre + root / 2 + side + bottom * slope, bottom),
        (centre + root / 2 + side + top * slope, top),
        (centre - root / 2 - side - top * slope, top),
    ]


def _prism(frame, outline, low, high) -> Solid:
    """``outline``, across the seam, swept along the frame's x from ``low`` to ``high``."""
    start = Plane(
        origin=frame.from_local_coords(Vector(low, 0, 0)), x_dir=frame.y_dir, z_dir=frame.x_dir
    )
    return extrude(start * Polygon(*outline, align=None), amount=high - low).solids()[0]


# --- pegs: the axis runs along the plane's normal --------------------------------


def _pegs(section, plane, ahead, clearance, margin):
    """Pegs and their sockets, at most two, where the cut face has room.

    Each peg is round with one flat side, so that one peg alone, all a small face
    may have room for, keeps the halves from turning about it, on no more room
    than a round peg of its size needs.
    """
    normal = Vector(plane.z_dir).normalized()
    frame = Plane(origin=plane.origin, x_dir=plane.x_dir, z_dir=normal)
    joints, taken = [], []
    for spot, edge in _roomy_spots(section, frame):
        radius = min(_PEG_MOST / 2, edge - clearance - margin)
        if radius < _PEG_LEAST / 2:
            break
        if any(
            (spot - other).length < radius + other_radius + 2 * clearance + margin
            for other, other_radius in taken
        ):
            continue
        made = _peg(frame, ahead, spot, radius, clearance, margin)
        if made is not None:
            joints.append(made)
            taken.append((spot, radius))
        if len(joints) == _JOINTS_MOST:
            break
    return joints


def _roomy_spots(section, frame):
    """Points on the cut face with how far each lies from the face's edge, farthest first."""
    if not section:
        return []
    corners = [frame.to_local_coords(face).bounding_box() for face in section]
    x0, x1 = min(c.min.X for c in corners), max(c.max.X for c in corners)
    y0, y1 = min(c.min.Y for c in corners), max(c.max.Y for c in corners)
    spots = []
    for i in range(_GRID + 1):
        for j in range(_GRID + 1):
            local = Vector(x0 + (x1 - x0) * i / _GRID, y0 + (y1 - y0) * j / _GRID, 0)
            at = frame.from_local_coords(local)
            point = Vertex(at.X, at.Y, at.Z)
            for face in section:
                if point.distance_to(face) < _EPS:
                    spots.append((local, min(point.distance_to(edge) for edge in face.edges())))
                    break
    spots.sort(key=lambda found: -found[1])
    return spots


def _peg(frame, ahead, spot, radius, clearance, margin):
    """A peg ``radius`` round at ``spot`` and its socket, shortened until the half
    holds them, or None."""
    length = 2 * radius
    while length >= radius:
        peg = _flatted(frame, spot, radius, 0.0, 0.0, length)
        socket = _flatted(frame, spot, radius, clearance, -_OVERCUT, length + clearance)
        room = _flatted(frame, spot, radius, clearance + margin, 0.0, length + clearance + margin)
        if _fills(ahead, room):
            return peg, socket
        length /= 2
    return None


def _flatted(frame, spot, radius, grow, start, end) -> Solid:
    """A round peg ``radius`` round with its flat side toward the frame's +y, grown
    by ``grow`` all round, standing at ``spot`` from ``start`` to ``end`` along the
    frame's z.

    Grown, it is the round grown and the flat moved out by the same amount: a
    little more than the peg grown evenly, at the two corners of the flat only.
    """
    origin = frame.from_local_coords(Vector(spot.X, spot.Y, start))
    base = Plane(origin=origin, x_dir=frame.x_dir, z_dir=frame.z_dir)
    height, outer, flat = end - start, radius + grow, radius * _FLAT + grow
    round_ = Cylinder(outer, height, align=(Align.CENTER, Align.CENTER, Align.MIN))
    cutter = Box(2 * (outer + _OVERCUT), 2 * outer, height + 2 * _OVERCUT)
    past_flat = Pos(0, flat + outer, height / 2) * cutter
    return (base * (round_ - past_flat)).solids()[0]
