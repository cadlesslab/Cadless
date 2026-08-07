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

from cadless.config import settings


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
