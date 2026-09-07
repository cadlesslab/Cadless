"""Catalog item thumbnails: headless mesh-to-PNG rendering (#21).

This runs inside containers with no GL context guarantee, so the
primary in-process renderer is a pure-software rasterizer: it reads the
tessellated geometry straight from a baked STL/OBJ artifact, projects it with a
fixed orthographic isometric camera fitted to the model's bounding box, shades
each facet with a single directional light, and paints depth-sorted polygons
(painter's algorithm) onto a transparent canvas with Pillow. numpy + Pillow are
already runtime dependencies — no GL, no trimesh.

Renderers are chained through :data:`RENDERERS`: a GL-backed renderer can be
prepended later and :func:`render_thumbnail` will fall through to the software
path wherever a GL context is unavailable.
"""

from __future__ import annotations

import io
import struct
from collections.abc import Callable, Sequence
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from cadless.catalog.manifest import CatalogManifest

# A renderer takes (triangles (n,3,3) float64, out_path, size) and writes a PNG.
Renderer = Callable[[np.ndarray, Path, int], None]

DEFAULT_SIZE = 384

_Z_AXIS = np.array([0.0, 0.0, 1.0])
_Y_AXIS = np.array([0.0, 1.0, 0.0])

# Camera directions, Z-up, each pointing from the model toward the camera. The
# names are shared with the interactive viewport's own view vocabulary so one
# word means one orientation across the product; the vectors are not, because
# that viewport works in a Y-up space. A mirror test holds the names together.
VIEW_EYES: dict[str, tuple[float, float, float]] = {
    "iso": (1.0, -1.0, 0.75),
    "front": (0.0, -1.0, 0.0),
    "back": (0.0, 1.0, 0.0),
    "left": (-1.0, 0.0, 0.0),
    "right": (1.0, 0.0, 0.0),
    "top": (0.0, 0.0, 1.0),
    "bottom": (0.0, 0.0, -1.0),
}

# Front, right and top read a part's three principal faces, and the isometric
# adds the corner that shows how they meet. Four is where this starts rather
# than a measured optimum — the count is a setting.
DEFAULT_VIEWS: tuple[str, ...] = ("front", "right", "top", "iso")

# Mesh artifact kinds the renderer can read, in preference order, and where an
# item's thumbnail lands relative to its own directory.
_THUMBNAIL_SOURCES = ("stl", "obj")
_THUMBNAIL_REL = "artifacts/thumbnail.png"
_SUPERSAMPLE = 2  # draw at 2x then downscale for cheap antialiasing
_MARGIN = 0.08  # bbox-fit margin as a fraction of the canvas
_BASE_RGB = np.array([116, 150, 185], dtype=np.float64)  # steel blue-gray

# 50-byte binary STL facet record: normal, 3 vertices, attribute byte count.
_STL_FACET = np.dtype(
    [
        ("normal", "<f4", (3,)),
        ("verts", "<f4", (3, 3)),
        ("attr", "<u2"),
    ]
)


# --------------------------------------------------------------------------- #
# mesh loading
# --------------------------------------------------------------------------- #


def load_mesh(path: Path) -> np.ndarray:
    """Triangles of an STL or OBJ file as an ``(n, 3, 3)`` float array."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".stl":
        return _load_stl(path)
    if suffix == ".obj":
        return _load_obj(path)
    raise ValueError(f"unsupported mesh format for thumbnails: {path.name}")


def _load_stl(path: Path) -> np.ndarray:
    data = path.read_bytes()
    # ASCII STLs start with "solid" and actually contain facet syntax; binary
    # headers may also start with "solid", so require the keyword too.
    if data[:5].lower() == b"solid" and b"facet" in data:
        floats = []
        for line in data.decode(errors="replace").splitlines():
            parts = line.split()
            if parts[:1] == ["vertex"]:
                floats.append([float(x) for x in parts[1:4]])
        return np.array(floats, dtype=np.float64).reshape(-1, 3, 3)
    if len(data) < 84:
        raise ValueError(f"not a valid STL file: {path}")
    (count,) = struct.unpack_from("<I", data, 80)
    facets = np.frombuffer(data, dtype=_STL_FACET, count=count, offset=84)
    return facets["verts"].astype(np.float64)


def _load_obj(path: Path) -> np.ndarray:
    verts: list[list[float]] = []
    tris: list[tuple[int, int, int]] = []
    for line in path.read_text(errors="replace").splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "v":
            verts.append([float(x) for x in parts[1:4]])
        elif parts[0] == "f":
            # "f v", "f v/vt", "f v/vt/vn", "f v//vn"; negative = relative
            idx = [int(p.split("/")[0]) for p in parts[1:]]
            idx = [i - 1 if i > 0 else len(verts) + i for i in idx]
            for a, b in zip(idx[1:-1], idx[2:], strict=True):  # fan-triangulate
                tris.append((idx[0], a, b))
    if not tris:
        return np.empty((0, 3, 3), dtype=np.float64)
    return np.asarray(verts, dtype=np.float64)[np.array(tris)]


# --------------------------------------------------------------------------- #
# software renderer (the headless-safe primary path)
# --------------------------------------------------------------------------- #


def _view_basis(view: str) -> np.ndarray:
    """Rows: right / up / toward-camera for one named Z-up view."""
    try:
        eye = np.array(VIEW_EYES[view], dtype=np.float64)
    except KeyError:
        known = ", ".join(sorted(VIEW_EYES))
        raise ValueError(f"unknown view {view!r} (known: {known})") from None
    eye /= np.linalg.norm(eye)
    # cross(Z, eye) collapses to the zero vector when the camera looks straight
    # down the Z axis — precisely the top and bottom views. Swapping the
    # reference axis for those keeps the basis defined instead of dividing by
    # zero and rendering a frame of NaN.
    reference = _Y_AXIS if abs(float(eye @ _Z_AXIS)) > 0.999 else _Z_AXIS
    right = np.cross(reference, eye)
    right /= np.linalg.norm(right)
    return np.stack([right, np.cross(eye, right), eye])


def _isometric_basis() -> np.ndarray:
    """The isometric basis — the camera a single-image render uses."""
    return _view_basis("iso")


# The light is fixed in **world** space, so a facet's shade is a property of the
# facet rather than of the camera. Held in view space it rotates with the eye:
# every view is then lit identically relative to its own camera, which strips
# the set of the one cue that tells two views apart when their silhouettes
# agree. The value is the view-space vector the single isometric render used,
# mapped back out through that view's own basis — so the thumbnail the publish
# path renders is bit-for-bit what it was, and every other view gains a cue.
_WORLD_LIGHT = np.array([-0.25, 0.45, 0.86]) @ _view_basis("iso")
_WORLD_LIGHT /= np.linalg.norm(_WORLD_LIGHT)


def _projected_extent(tris: np.ndarray, basis: np.ndarray) -> float:
    """The larger screen-space side of the bbox this view projects onto."""
    xy = (tris.reshape(-1, 3) @ basis.T)[:, :2]
    lo, hi = xy.min(axis=0), xy.max(axis=0)
    return float(max((hi - lo).max(), 1e-9))


def _rasterize(tris: np.ndarray, size: int, basis: np.ndarray, extent: float) -> Image.Image:
    """Project, shade and paint the triangles at a caller-chosen scale."""
    view = (tris.reshape(-1, 3) @ basis.T).reshape(-1, 3, 3)
    xy, depth = view[..., :2], view[..., 2].mean(axis=1)

    lo, hi = xy.reshape(-1, 2).min(axis=0), xy.reshape(-1, 2).max(axis=0)
    canvas = size * _SUPERSAMPLE
    scale = canvas * (1 - 2 * _MARGIN) / extent
    screen = (xy - (lo + hi) / 2) * scale
    screen[..., 1] *= -1  # image y grows downward
    screen += canvas / 2

    # flat Lambert shading from the facet normals (abs: no backface culling)
    a, b = tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0]
    normals = np.cross(a, b)
    lengths = np.linalg.norm(normals, axis=1, keepdims=True)
    normals = np.divide(normals, lengths, out=np.zeros_like(normals), where=lengths > 0)
    shade = 0.35 + 0.65 * np.abs(normals @ _WORLD_LIGHT)
    colors = np.clip(_BASE_RGB * shade[:, None], 0, 255).astype(np.uint8)

    img = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    for i in np.argsort(depth):  # farthest first
        r, g, bl = colors[i]
        draw.polygon([tuple(p) for p in screen[i]], fill=(int(r), int(g), int(bl), 255))
    return img.resize((size, size), Image.LANCZOS)


def render_software(
    tris: np.ndarray,
    out_path: Path,
    size: int,
    *,
    basis: np.ndarray | None = None,
    extent: float | None = None,
) -> None:
    """Orthographic bbox-fitted shaded render via numpy + Pillow (no GL).

    ``basis`` and ``extent`` are keyword-only with defaults so this keeps
    satisfying the three-argument :data:`Renderer` contract. Passing an
    ``extent`` measured across several views is what makes a set of frames
    share one scale instead of each filling its own.
    """
    if tris.size == 0:
        raise ValueError("mesh has no triangles")
    basis = _isometric_basis() if basis is None else basis
    extent = _projected_extent(tris, basis) if extent is None else extent
    _rasterize(tris, size, basis, extent).save(out_path, format="PNG")


def render_views(
    mesh_path: Path,
    views: Sequence[str] = DEFAULT_VIEWS,
    size: int = DEFAULT_SIZE,
) -> list[tuple[str, bytes]]:
    """Render ``mesh_path`` once per named view, as ``(view, PNG bytes)``.

    Every frame is fitted to one extent measured across the whole set, so a
    reader can compare them as one object; fitted per view each would fill its
    own frame and the same edge would appear at a different size in each.
    Raises ``ValueError`` for an unknown view name or an unloadable mesh.
    """
    tris = load_mesh(Path(mesh_path))
    if tris.size == 0:
        raise ValueError(f"mesh has no triangles: {mesh_path}")
    bases = [(name, _view_basis(name)) for name in views]
    if not bases:
        return []
    extent = max(_projected_extent(tris, basis) for _, basis in bases)
    shots: list[tuple[str, bytes]] = []
    for name, basis in bases:
        buf = io.BytesIO()
        _rasterize(tris, size, basis, extent).save(buf, format="PNG")
        shots.append((name, buf.getvalue()))
    return shots


# GL-capable renderers can be prepended here; the software one always works.
RENDERERS: list[Renderer] = [render_software]


def render_thumbnail(mesh_path: Path, out_path: Path, size: int = DEFAULT_SIZE) -> Path:
    """Render ``mesh_path`` to a ``size``x``size`` PNG at ``out_path``.

    Tries each renderer in :data:`RENDERERS` in order and returns on the first
    success; raises ``RuntimeError`` when every renderer fails (with each
    failure noted) and ``ValueError`` for unloadable/empty meshes.
    """
    tris = load_mesh(Path(mesh_path))
    if tris.size == 0:
        raise ValueError(f"mesh has no triangles: {mesh_path}")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []
    for renderer in RENDERERS:
        try:
            renderer(tris, out_path, size)
            return out_path
        except Exception as exc:  # noqa: BLE001 — chain to the next renderer
            failures.append(f"{getattr(renderer, '__name__', renderer)}: {exc}")
    raise RuntimeError(f"every thumbnail renderer failed for {mesh_path}: {'; '.join(failures)}")


def render_item_thumbnail(item_dir: Path, manifest: CatalogManifest) -> str | None:
    """Render an item's thumbnail from its final step's mesh artifact (#21).

    Reads what a baked item already has rather than producing it: a project
    being written out comes through here with its meshes in hand and never
    executes anything. That is why this survived the move of the authoring
    stack to the private pipeline — it belongs to the reading side.

    **Kept for a build that publishes, and called by nothing here.** Its one
    caller left with the publish path, exactly as the write-side vocabulary in
    `catalog/pack.py` did; both are held for the same reason, and both are
    unreferenced in this tree on purpose rather than by oversight.

    The renderer chain is headless-safe (software rasterizer primary), so this
    works in containers with no GL context. Returns the manifest-relative PNG
    path, or ``None`` when the final step exported no readable mesh.
    """
    if not manifest.steps:
        return None
    final = manifest.steps[-1]
    for kind in _THUMBNAIL_SOURCES:
        rel = final.artifacts.get(kind)
        if rel and (item_dir / rel).exists():
            render_thumbnail(item_dir / rel, item_dir / _THUMBNAIL_REL)
            return _THUMBNAIL_REL
    return None
