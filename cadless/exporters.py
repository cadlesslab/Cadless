"""Artifact export: STEP, glTF/GLB, STL and OBJ.

- STEP: the authoritative engineering format, downloadable.
- GLB: tessellated mesh for the three.js viewport; deflection
  (mesh fineness) is configurable.
- STL / OBJ: mesh formats for 3D printing and generic interchange.

STEP/GLB/STL wrap the OCCT writers via build123d; build123d has no OBJ writer,
so ``export_obj`` is written directly from ``Shape.tessellate`` (a trivial
``v``/``f`` text format — no extra dependency). All exporters share the signature
``(result, out_dir, name="model") -> path`` so ``EXPORTERS`` can drive them
uniformly.
"""

from __future__ import annotations

import os
import re

from cadless.config import settings

#: How one part of a build is named, and how that name is read back.
#:
#: The process that writes these files and whatever later copies them in are not
#: the same code, and they have to agree exactly: the number in the name becomes
#: the ordinal the part is filed under. A name the reader cannot parse is a part
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


def export_step(result, out_dir: str, name: str = "model") -> str:
    """Write `result` to ``<out_dir>/<name>.step`` and return the path."""
    from build123d import export_step as _export_step

    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{name}.step")
    _export_step(result, path)
    return path


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
        result,
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
