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
it is: a script can call the function, and the module behind it lies behind a
dunder.
"""

from __future__ import annotations

import math

from build123d import (
    Align,
    Cylinder,
    Edge,
    GeomType,
    Keep,
    Plane,
    Polygon,
    Solid,
    Vector,
    Vertex,
    extrude,
)

#: Off square, each flank of a dovetail: enough to lock the halves across the
#: seam, shallow enough to print without support.
_FLANK_DEGREES = 15.0
#: A dovetail's root, across the seam, at most and at least (mm). Below the least
#: it is too weak to hold the halves together.
_ROOT_MOST, _ROOT_LEAST = 20.0, 3.0
#: How deep a dovetail reaches into the other half, per unit of its root.
_DEPTH_PER_ROOT = 0.6
#: A peg's diameter, at most and at least (mm).
_PEG_MOST, _PEG_LEAST = 8.0, 3.0
#: The shortest a joint may run along the axis the half goes in (mm).
_RUN_LEAST = 3.0
#: Joints per seam, at most: two far apart keep the halves from turning.
_JOINTS_MOST = 2
#: The clearance a printer can hold, in mm: the range a printer profile accepts.
_CLEARANCE_MOST = 2.0
#: Positions sampled across a cut face when looking for room for a joint.
_SAMPLES = 240
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
    sockets are open at both ends; along the plane's normal, it gives round pegs.
    Each joint sits where the cut face is solid along its whole run, its socket
    keeping a margin of ``max(1, 2 * clearance)`` mm from every edge and hole, and
    the socket is the tab grown by ``clearance`` on every side that meets it.

    Returns ``(behind, ahead)``: the half behind the plane's normal, which carries
    the tabs or pegs, and the half the normal points into, which carries the
    sockets. They are labelled from ``names``, or from the part's own label.

    Raises :class:`JoineryError`, saying what to change, when the clearance is
    outside what a printer holds, the axis is not one the plane allows, a side of
    the cut comes out in several pieces, or no joint fits on the cut face.
    """
    _check_clearance(clearance)
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
    section = _section(behind, plane)
    margin = max(1.0, 2 * clearance)
    if across <= _EPS:
        joints = _dovetails(section, plane, axis, ahead, clearance, margin)
    else:
        joints = _pegs(section, plane, ahead, clearance, margin)
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
    if not isinstance(clearance, int | float) or not 0.0 <= clearance <= _CLEARANCE_MOST:
        raise JoineryError(
            f"clearance must be a number of millimetres from 0 to {_CLEARANCE_MOST:g}, "
            f"the range a printer holds: got {clearance!r}"
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


def _section(behind: Solid, plane: Plane) -> list:
    """The faces the cut left on ``behind``: where a joint can stand."""
    normal = Vector(plane.z_dir).normalized()
    return [
        face
        for face in behind.faces()
        if face.geom_type == GeomType.PLANE
        and abs(abs(face.normal_at().dot(normal)) - 1) < _EPS
        and abs(plane.to_local_coords(face.center()).Z) < 1e-6
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


def _dovetails(section, plane, axis, ahead, clearance, margin):
    """Tabs and sockets for up to two dovetails swept along ``axis``.

    Positions across the cut face are sampled; where the line along ``axis``
    crosses the face in one run, unbroken by a hole, and the same run at
    neighbouring positions, the face is a band a dovetail can stand in.
    """
    frame = Plane(origin=plane.origin, x_dir=axis, z_dir=Vector(plane.z_dir).normalized())
    sites = []
    for low, high, start, end in _bands(section, frame):
        root = _widest_root(end - start, clearance, margin)
        if root < _ROOT_LEAST:
            continue
        half = _reach(root, clearance + margin)
        for centre in sorted({start + half, max(start + half, end - half)}):
            sites.append((centre, half, root, low, high))
    extent = _extent(ahead, frame)
    joints = []
    for centre, _half, root, low, high in _spread(sites):
        made = _dovetail(frame, ahead, centre, root, low, high, extent, clearance, margin)
        if made is not None:
            joints.append(made)
    return joints


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
    position from ``start`` to ``end`` across it.
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
        runs = _runs(section, frame, across, x0 - 1, x1 + 1)
        run = runs[0] if len(runs) == 1 and runs[0][1] - runs[0][0] >= _RUN_LEAST else None
        same = (
            current is not None
            and run is not None
            and abs(run[0] - current[0]) < 1e-3
            and abs(run[1] - current[1]) < 1e-3
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
        if merged and low <= merged[-1][1] + 1e-6:
            merged[-1] = (merged[-1][0], max(high, merged[-1][1]))
        else:
            merged.append((low, high))
    return merged


def _spread(sites):
    """At most two of ``sites``, as far apart as their sockets and margins allow.

    Each site is ``(centre, half, ...)``, ``half`` being how far its socket and
    margin reach to either side of the centre.
    """
    if len(sites) <= 1:
        return sites
    best, gap = [max(sites, key=lambda site: site[2])], 0.0
    for i, first in enumerate(sites):
        for second in sites[i + 1 :]:
            apart = abs(first[0] - second[0])
            if apart > gap and apart >= first[1] + second[1]:
                best, gap = [first, second], apart
    return best[:_JOINTS_MOST]


def _extent(ahead, frame) -> tuple[float, float]:
    box = frame.to_local_coords(ahead).bounding_box()
    return box.min.X - 1, box.max.X + 1


def _dovetail(frame, ahead, centre, root, low, high, extent, clearance, margin):
    """A tab and its socket at ``centre``, or None when the half cannot hold them."""
    while root >= _ROOT_LEAST:
        depth = root * _DEPTH_PER_ROOT
        tab = _prism(frame, _trapezoid(centre, root, depth, 0.0, 0.0), low, high)
        socket = _prism(frame, _trapezoid(centre, root, depth, clearance, 1.0), *extent)
        room = _prism(frame, _trapezoid(centre, root, depth, clearance + margin, 0.0), low, high)
        if _volume(ahead & room) >= room.volume * (1 - 1e-6):
            return tab, socket
        root *= 0.75
    return None


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
    """Pegs and their sockets, at most two, where the cut face has room."""
    normal = Vector(plane.z_dir).normalized()
    frame = Plane(origin=plane.origin, x_dir=plane.x_dir, z_dir=normal)
    spots = _roomy_spots(section, frame, clearance + margin)
    joints, taken = [], []
    for spot, room in spots:
        radius = min(_PEG_MOST / 2, room)
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


def _roomy_spots(section, frame, spare):
    """Points on the cut face with the radius a peg could take there, roomiest first."""
    if not section:
        return []
    corners = [frame.to_local_coords(face).bounding_box() for face in section]
    x0, x1 = min(c.min.X for c in corners), max(c.max.X for c in corners)
    y0, y1 = min(c.min.Y for c in corners), max(c.max.Y for c in corners)
    count = 24
    spots = []
    for i in range(count + 1):
        for j in range(count + 1):
            local = Vector(x0 + (x1 - x0) * i / count, y0 + (y1 - y0) * j / count, 0)
            at = frame.from_local_coords(local)
            point = Vertex(at.X, at.Y, at.Z)
            for face in section:
                if point.distance_to(face) < 1e-9:
                    edge_room = min(point.distance_to(edge) for edge in face.edges())
                    spots.append((local, edge_room - spare))
                    break
    spots.sort(key=lambda found: -found[1])
    return spots


def _peg(frame, ahead, spot, radius, clearance, margin):
    """A peg at ``spot`` and its socket, or None when the half cannot hold them."""
    length = 2 * radius
    while length >= radius:
        peg = _cylinder(frame, spot, radius, 0.0, length)
        socket = _cylinder(frame, spot, radius + clearance, -1.0, length + clearance)
        room = _cylinder(frame, spot, radius + clearance + margin, 0.0, length + clearance + margin)
        if _volume(ahead & room) >= room.volume * (1 - 1e-6):
            return peg, socket
        length /= 2
    return None


def _cylinder(frame, spot, radius, start, end) -> Solid:
    base = Plane(origin=frame.from_local_coords(Vector(spot.X, spot.Y, start)), z_dir=frame.z_dir)
    upright = Cylinder(radius, end - start, align=(Align.CENTER, Align.CENTER, Align.MIN))
    return (base * upright).solids()[0]
