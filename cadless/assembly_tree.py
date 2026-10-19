"""A build read as the tree build123d makes: named parts, each placed once.

A script's ``result`` can be a Compound whose children are its parts and
sub-assemblies, each with a label, a colour and a location relative to the one
above it. ``result.solids()`` returns the same solids, in the order the part
index is defined by, but with the labels and colours gone. So the geometry and
the order are taken from ``solids()``, and the names, paths and colours from a
walk of the children, which pairs each label with a solid only where the
compound's own shape shows which solid that label belongs to.

build123d and OCCT are imported where a shape is read, not at module level:
the payload is plain data, and a reader of it never needs the kernel.
"""

from __future__ import annotations

import itertools
import json
import logging
import math
import sys
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

#: The longest name a part or a group keeps. A label is whatever the build's code
#: assigned, and every name is carried into the payload, the store and the
#: whole-assembly files, so it is cut once, where it is read.
NAME_MAX = 128

#: Bounds on a part list that is kept: how many parts, how deep a path, and its
#: size as JSON. The list arrives from a worker, which need be neither this build
#: of the engine nor honest, and is read back on every read of the version.
MAX_PARTS = 2000
MAX_DEPTH = 16
MAX_RECORD_BYTES = 256 * 1024


def unnamed(index: int) -> str:
    """The name of a part its build gave none: zero-based, as the part files are."""
    return f"part{index}"


@dataclass(frozen=True)
class PartNode:
    """One part of a build: one solid of a leaf, in the world frame."""

    index: int  # position in ``result.solids()``
    name: str  # the leaf's label, ``label_k`` for a leaf of several, else ``part<index>``
    path: tuple[str, ...]  # labels of the labelled groups above it, root first
    color: tuple[float, float, float, float] | None
    solid: Any  # a build123d Solid with its placement baked in: identity location
    # Which group each label of ``path`` names, by the order the walk met them:
    # two sibling sub-assemblies may share a label and are still two groups.
    group_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class BuildTree:
    name: str  # the root's label, or ""
    parts: list[PartNode]
    groups: list[tuple[str, tuple[str, ...]]]  # each labelled group and its path, pre-order

    def as_payload(self) -> dict:
        """The tree without its geometry, as JSON-ready primitives."""
        return {
            "name": self.name,
            "parts": [
                {
                    "index": p.index,
                    "name": p.name,
                    "path": list(p.path),
                    "color": list(p.color) if p.color is not None else None,
                }
                for p in self.parts
            ],
            "groups": [{"name": name, "path": list(path)} for name, path in self.groups],
        }


@dataclass(frozen=True)
class _Leaf:
    solid: Any  # the leaf's solid as an OCCT shape, placed as ``result.solids()`` places it
    name: str  # "" where the leaf has no label: the part's index names it
    path: tuple[str, ...]
    group_ids: tuple[int, ...]
    color: tuple[float, float, float, float] | None


def _children(node) -> list:
    return list(getattr(node, "children", ()) or ())


def _label(node) -> str:
    """A node's label as a name: text, at most ``NAME_MAX`` long, "" for none.

    ``str`` rather than trusting the type: build123d keeps whatever the code
    assigned, and a number there is a name the code meant, not a fault.
    """
    label = getattr(node, "label", None)
    if label is None or label == "":
        return ""
    return str(label)[:NAME_MAX]


def _rgba(color) -> tuple[float, float, float, float] | None:
    return None if color is None else tuple(float(c) for c in color)


def _sub_shapes(wrapped) -> list:
    from OCP.TopoDS import TopoDS_Iterator

    found = []
    iterator = TopoDS_Iterator(wrapped)
    while iterator.More():
        found.append(iterator.Value())
        iterator.Next()
    return found


def _solids_of(wrapped) -> list:
    from OCP.TopAbs import TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer

    found = []
    explorer = TopExp_Explorer(wrapped, TopAbs_SOLID)
    while explorer.More():
        found.append(explorer.Current())
        explorer.Next()
    return found


def _form(wrapped) -> tuple:
    """A shape's type, its face, edge and vertex counts, and its volume."""
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_VERTEX
    from OCP.TopExp import TopExp
    from OCP.TopTools import TopTools_IndexedMapOfShape

    counts = []
    for kind in (TopAbs_FACE, TopAbs_EDGE, TopAbs_VERTEX):
        found = TopTools_IndexedMapOfShape()
        TopExp.MapShapes_s(wrapped, kind, found)
        counts.append(found.Extent())
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(wrapped, props)
    return (wrapped.ShapeType(), *counts), props.Mass()


def _same_form(a, b) -> bool:
    (a_counts, a_volume), (b_counts, b_volume) = _form(a), _form(b)
    return a_counts == b_counts and math.isclose(a_volume, b_volume, rel_tol=1e-6, abs_tol=1e-9)


def _paired(children: list, subs: list) -> bool:
    """Whether a node's children can be read off its sub-shapes by position.

    Either the node is in step with its children -- each child's shape is the
    one in its own slot -- or the node is a copy, sharing no shape with them
    but holding, slot by slot, a shape of the same form (type, number of faces,
    edges and vertices, and volume, which moving and turning keep). Anything
    else means the node's shape went stale -- a solid moved to another parent
    without the node being rebuilt -- and position no longer says which solid a
    label belongs to. A sub-assembly scaled as a copy fails the volume check and
    loses its names, which is the safe way to be wrong.
    """
    pairs = list(zip(children, subs, strict=True))
    if all(child.wrapped.IsPartner(sub) for child, sub in pairs):
        return True
    if any(child.wrapped.IsPartner(sub) for child in children for sub in subs):
        return False
    return all(_same_form(child.wrapped, sub) for child, sub in pairs)


def _leaves(shape) -> list[_Leaf]:
    """Every solid of every leaf, in the pre-order of the leaves.

    A leaf is a node with no children. One holding several solids -- a boolean
    that left two bodies -- gives one entry per solid, as ``solids()`` does, so a
    shape built that way keeps its part count.

    The labels are read off the build123d children, and the solids off the
    node's own OCCT shape, the two paired by position. They are not the same
    objects: a sub-assembly placed with ``Pos(...) * kit`` is a copy whose shape
    holds new solids that share no shape with its children. A node whose
    children and sub-shapes do not pair up one to one (see ``_paired``) gives
    its solids with no names, so a mismatch there costs only that subtree its
    names.
    """
    found: list[_Leaf] = []
    group_ids = itertools.count()

    def walk(node, wrapped, path: tuple[str, ...], ids: tuple[int, ...]) -> None:
        children = _children(node)
        label = _label(node)
        if children:
            inner, inner_ids = ((*path, label), (*ids, next(group_ids))) if label else (path, ids)
            subs = _sub_shapes(wrapped)
            if len(subs) == len(children) and _paired(children, subs):
                for child, sub in zip(children, subs, strict=True):
                    walk(child, sub, inner, inner_ids)
                return
            found.extend(_Leaf(solid, "", inner, inner_ids, None) for solid in _solids_of(wrapped))
            return
        solids = _solids_of(wrapped)
        for k, solid in enumerate(solids):
            name = label if len(solids) == 1 else (f"{label}_{k}"[:NAME_MAX] if label else "")
            found.append(_Leaf(solid, name, path, ids, _rgba(node.color)))

    walk(shape, shape.wrapped, (), ())
    return found


def _groups(shape) -> list[tuple[str, tuple[str, ...]]]:
    found: list[tuple[str, tuple[str, ...]]] = []

    def walk(node, path: tuple[str, ...]) -> None:
        children = _children(node)
        if not children:
            return
        label = _label(node)
        if label:
            found.append((label, path))
            path = (*path, label)
        for child in children:
            walk(child, path)

    walk(shape, ())
    return found


def _bake(solid, scale: float):
    """``solid`` with its placement moved into its geometry, then scaled.

    A solid taken out of a compound keeps a Location of its own, and every
    consumer that is handed one has to compose it correctly. Baked, the
    geometry already sits where the part sits in the assembled model. The
    scale comes after the placement and, with no Location left, is about the
    world origin: it converts every coordinate of the part, its position
    included, into the units the files are written in.
    """
    from build123d import Solid
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.TopLoc import TopLoc_Location

    wrapped = solid.wrapped
    placement = wrapped.Location().Transformation()
    baked = Solid(
        BRepBuilderAPI_Transform(wrapped.Located(TopLoc_Location()), placement, True).Shape()
    )
    return baked if scale == 1.0 else baked.scale(scale)


def build_tree(result, scale: float = 1.0) -> BuildTree:
    """Read ``result`` as a tree of named parts, each baked into the world frame.

    The parts are ``result.solids()``, in that order. A part the walk could not
    pair with a label is ``part<index>`` -- zero-based, as the part index and
    the part files are -- and if the walk does not account for exactly these
    solids in this order, every part is, with no path. Reading
    the names is the one step allowed to fail: the geometry is not, and a
    solid that cannot be read or baked raises as it would anywhere else.
    """
    solids = list(result.solids())
    try:
        leaves = _leaves(result)
    except Exception:  # noqa: BLE001 - names are best-effort; the parts are not
        logger.warning("could not read the build's part names", exc_info=True)
        leaves = []
    lined_up = len(leaves) == len(solids) and all(
        leaf.solid.IsPartner(solid.wrapped) for leaf, solid in zip(leaves, solids, strict=True)
    )
    parts = []
    for index, solid in enumerate(solids):
        leaf = leaves[index] if lined_up else None
        parts.append(
            PartNode(
                index=index,
                name=(leaf.name if leaf is not None and leaf.name else unnamed(index)),
                path=leaf.path if leaf is not None else (),
                color=leaf.color if leaf is not None else None,
                solid=_bake(solid, scale),
                group_ids=leaf.group_ids if leaf is not None else (),
            )
        )
    return BuildTree(
        name=_label(result),
        parts=parts,
        groups=_groups(result) if lined_up else [],
    )


def _is_name(value: Any) -> bool:
    return isinstance(value, str) and 0 < len(value) <= NAME_MAX


def _is_component(value: Any) -> bool:
    # Compared rather than passed to ``math.isfinite``: JSON hands an integer
    # back as an ``int`` of any size, which that raises on. NaN fails both sides.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return -sys.float_info.max <= value <= sys.float_info.max


def _is_colour(value: Any) -> bool:
    return isinstance(value, list) and len(value) == 4 and all(map(_is_component, value))


def parts_record(raw: Any) -> list[dict] | None:
    """``raw`` as a part list to keep, or ``None`` where it is not one.

    Each entry comes back with exactly ``index``, ``name``, ``path`` and
    ``color``, the indices counting from zero in order; keys it does not know
    are dropped, and an absent ``color`` is none. Anything else -- a name or
    path missing or of the wrong type, an index out of place, a name or a path
    label empty or past ``NAME_MAX``, a colour that is not four finite numbers,
    a list past the bounds above -- refuses the whole list rather than the
    entry, because a list with a hole in it numbers the parts after the hole
    wrongly.
    """
    if not isinstance(raw, list) or len(raw) > MAX_PARTS:
        return None
    record: list[dict] = []
    for position, entry in enumerate(raw):
        if not isinstance(entry, dict):
            return None
        index, name, path, color = (entry.get(k) for k in ("index", "name", "path", "color"))
        if type(index) is not int or index != position or not _is_name(name):
            return None
        if not isinstance(path, list) or len(path) > MAX_DEPTH or not all(map(_is_name, path)):
            return None
        if color is not None and not _is_colour(color):
            return None
        record.append(
            {
                "index": index,
                "name": name,
                "path": list(path),
                "color": None if color is None else [float(c) for c in color],
            }
        )
    if len(json.dumps(record)) > MAX_RECORD_BYTES:
        return None
    return record
