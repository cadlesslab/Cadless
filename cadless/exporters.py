"""Artifact export: STEP, glTF/GLB, STL and OBJ.

- STEP: the authoritative engineering format, downloadable.
- GLB: tessellated mesh for the three.js viewport; deflection
  (mesh fineness) is configurable.
- STL / OBJ: mesh formats for 3D printing and generic interchange.

STEP/GLB/STL wrap the OCCT writers via build123d; build123d has no OBJ writer,
so ``export_obj`` is written directly from ``Shape.tessellate`` (a trivial
``v``/``f`` text format — no extra dependency). Every member of ``EXPORTERS``
shares the signature ``(result, out_dir, name="model") -> path`` so the registry
can drive them uniformly; ``export_assembly``, one file of each for the whole
build, is outside it.
"""

from __future__ import annotations

import copy
import os
import re
from pathlib import Path

from cadless.config import settings

#: How one part of a build is named, and how that name is read back.
#:
#: The process that writes these files and whatever later copies them in are not
#: the same code, and they have to agree exactly: the order of the numbers in
#: these names is the order the parts are filed in, and so the ordinal each one
#: ends up with. A name the reader cannot parse is a part
#: either dropped or filed under another part's number, and neither failure says
#: anything at the time. So both halves live here, together, rather than as a
#: format in one place and a pattern that has to match it in another.
_PART_STEM = re.compile(r"^model_p(\d+)$")


def part_name(index: int, total: int) -> str:
    """The base filename for one part of a build, without its extension.

    A one-solid build keeps ``model``, byte for byte what every build wrote before
    parts existed. That is the upgrade path: an installation that never asks for
    an assembly sees the tree it has always had, so anything that went looking for
    that name still finds it. Numbering starts at zero and is not padded, because
    the reader parses the number rather than sorting the string.
    """
    return "model" if total == 1 else f"model_p{index}"


def part_index(stem: str) -> int | None:
    """The part number carried by an exported file's stem, or ``None``."""
    match = _PART_STEM.match(stem)
    return int(match.group(1)) if match else None


def exported_parts(src_dir: Path, kind: str) -> list[Path]:
    """Every file of ``kind`` this build wrote, in part order.

    Beside the writer and the pattern on purpose: a reader kept elsewhere is a
    second place the naming can be spelled, and the two drifting apart is not a
    failure anything raises on -- each half stays individually well formed while
    a part is dropped or filed under another's number. Everything that asks what
    a build wrote asks here, so they cannot disagree.

    Ordered on the number parsed out of the name, never on the name itself: as
    text ``model_p10`` sorts before ``model_p2``, and since this order decides the
    ordinal each file is filed under, a lexicographic sort would quietly file part
    ten as part one. A file whose stem carries no number is skipped rather than
    guessed at.

    A one-solid build wrote ``model.{kind}`` and is returned as the single part it
    is. Finding the plain name settles the question, so the two namings sharing a
    directory would be a build written on top of another's leftovers -- which the
    export step is responsible for not leaving. What this does in that case is
    pinned by a test rather than left to the reader.
    """
    single = src_dir / f"model.{kind}"
    if single.exists():
        return [single]
    numbered: list[tuple[int, Path]] = []
    for path in src_dir.glob(f"model_p*.{kind}"):
        index = part_index(path.stem)
        if index is not None:
            numbered.append((index, path))
    return [path for _, path in sorted(numbered)]


def export_step(result, out_dir: str, name: str = "model") -> str:
    """Write `result` to ``<out_dir>/<name>.step`` and return the path."""
    from build123d import export_step as _export_step

    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{name}.step")
    _export_step(result, path)
    return path


def _location_as_a_child(shape):
    """`shape` with any Location of its own moved one level down, as a child's.

    build123d turns a Z-up model into glTF's Y-up by multiplying the rotation
    onto the right of the exported shape's own Location. That is the right
    side for the geometry and the wrong side for the Location: a shape that sits
    where it is because of its Location keeps its geometry turned but has its
    placement written unturned, so it is drawn somewhere else -- as a one-part
    build placed with ``Pos(...) * shape`` would be. Inside a compound with no
    Location of its own, the turn lands on the compound and the shape's
    Location is composed beneath it, which is the order a viewer needs.

    A shape already at the identity is handed back as it is. Its file is then
    the one it has always been, and a level of nesting added to every build
    would change files nothing was wrong with.

    The compound holds a deep copy, never the caller's object. build123d moves
    a child into the compound that adopts it, which would take the shape out of
    any assembly it belongs to; and a shallow copy shares the caller's
    underlying OCCT shape, which OCCT freezes once it is put into a compound,
    after which nothing can be added to it or taken out.
    """
    from build123d import Compound

    if shape.location.wrapped.IsIdentity():
        return shape
    return Compound(children=[copy.deepcopy(shape)])


def export_glb(
    result,
    out_dir: str,
    name: str = "model",
    *,
    deflection: float | None = None,
) -> str:
    """Tessellate `result` to a binary glTF (.glb) and return the path.

    `deflection` is the OCCT linear deflection (smaller = finer mesh); defaults to
    ``settings.gltf_deflection``.
    """
    from build123d import export_gltf

    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{name}.glb")
    export_gltf(
        _location_as_a_child(result),
        path,
        binary=True,
        linear_deflection=deflection if deflection is not None else settings.gltf_deflection,
    )
    return path


def export_stl(
    result,
    out_dir: str,
    name: str = "model",
    *,
    tolerance: float | None = None,
) -> str:
    """Write `result` to a binary STL (.stl) and return the path.

    `tolerance` is the OCCT linear deflection (smaller = finer mesh); defaults to
    ``settings.gltf_deflection`` so STL and GLB share one mesh-fineness knob.
    """
    from build123d import export_stl as _export_stl

    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{name}.stl")
    _export_stl(
        result,
        path,
        tolerance=tolerance if tolerance is not None else settings.gltf_deflection,
    )
    return path


def export_obj(
    result,
    out_dir: str,
    name: str = "model",
    *,
    tolerance: float | None = None,
) -> str:
    """Write `result` to a Wavefront OBJ (.obj) and return the path.

    build123d has no OBJ writer, so we tessellate to triangles and emit the
    minimal ``v``/``f`` form (OBJ face indices are 1-based). `tolerance` is the
    tessellation linear deflection; defaults to ``settings.gltf_deflection``.
    """
    tol = tolerance if tolerance is not None else settings.gltf_deflection
    vertices, triangles = result.tessellate(tol)

    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{name}.obj")
    with open(path, "w") as fh:
        fh.write(f"# Cadless export: {name}\n")
        for v in vertices:
            fh.write(f"v {v.X:.6g} {v.Y:.6g} {v.Z:.6g}\n")
        for tri in triangles:
            a, b, c = tri  # 0-based -> OBJ is 1-based
            fh.write(f"f {a + 1} {b + 1} {c + 1}\n")
    return path


# Registry of all artifact exporters, keyed by artifact ``kind``. The worker
# iterates this to produce every format; the API serves the same kinds.
EXPORTERS = {
    "step": export_step,
    "glb": export_glb,
    "stl": export_stl,
    "obj": export_obj,
}

#: The whole-assembly files, keyed by artifact kind. Not in ``EXPORTERS``: each
#: is one file for the whole build, not one per part, so the per-part naming
#: above does not describe it.
ASSEMBLY_FILES = {"assembly_glb": "assembly.glb", "assembly_step": "assembly.step"}


def _assembly_compound(tree):
    """The tree's parts as one Compound: each named and coloured, under its groups.

    Built from the parts rather than from the build's own ``result``, because
    the parts are already what the part files hold -- placed, scaled and in
    part order -- and a file of the whole should agree with the files of its
    pieces. Each part is a deep copy, so putting it in a compound neither moves
    nor freezes the tree's own.

    Groups are told apart by ``group_ids`` rather than by label: two sibling
    sub-assemblies may share one, and keyed by label they would merge into one
    node and pull the second one's parts ahead of what lies between them. A
    group's parts are consecutive in part order, so each group is one run.
    """
    from build123d import Color, Compound

    def named(part):
        solid = copy.deepcopy(part.solid)
        solid.label = part.name
        if part.color is not None:
            solid.color = Color(*part.color)
        return solid

    def gather(members: list, depth: int) -> list:
        children: list = []
        start = 0
        while start < len(members):
            part = members[start]
            if len(part.group_ids) == depth:
                children.append(named(part))
                start += 1
                continue
            group = part.group_ids[depth]
            end = start
            while (
                end < len(members)
                and len(members[end].group_ids) > depth
                and members[end].group_ids[depth] == group
            ):
                end += 1
            inner = gather(members[start:end], depth + 1)
            children.append(Compound(children=inner, label=part.path[depth]))
            start = end
        return children

    parts = list(tree.parts)
    # The root is a group of its own when the build labelled its result: the
    # first group the walk meets, holding every part.
    if tree.name and parts and all(part.group_ids[:1] == (0,) for part in parts):
        return Compound(children=gather(parts, 1), label=tree.name)
    return Compound(children=gather(parts, 0), label=tree.name)


def export_assembly(tree, out_dir: str) -> dict[str, str]:
    """Write the whole build as one GLB and one STEP; return ``{kind: path}``.

    The GLB carries a node per part and per labelled group, named as the tree
    names them, and a material per colour; the STEP names its products the same
    way. Written at the GLB deflection every part file is written at.

    Raises when the GLB is not written: build123d reports that by its return
    value, and a path returned for a file that is not there would be served as
    one that is.
    """
    from build123d import export_gltf
    from build123d import export_step as _export_step

    compound = _assembly_compound(tree)
    os.makedirs(out_dir, exist_ok=True)
    paths = {kind: os.path.join(out_dir, name) for kind, name in ASSEMBLY_FILES.items()}
    if not export_gltf(
        compound, paths["assembly_glb"], binary=True, linear_deflection=settings.gltf_deflection
    ):
        raise RuntimeError(f"build123d did not write {paths['assembly_glb']}")
    _export_step(compound, paths["assembly_step"])
    return paths
