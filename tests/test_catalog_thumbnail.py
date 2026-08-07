"""Thumbnail renderer tests (catalog discovery, #21).

The renderer is a pure-software path (numpy + Pillow) because the bake step
runs headless in containers with no GL context guarantee. These tests exercise
mesh parsing (binary/ascii STL, OBJ), the PNG output, and the renderer chain.
"""

import struct
from pathlib import Path

import numpy as np
import pytest

from cadless.catalog import thumbnail as thumb

# --------------------------------------------------------------------------- #
# mesh fixtures
# --------------------------------------------------------------------------- #

# A unit tetrahedron: 4 triangular faces.
_TET_TRIS = [
    ((0, 0, 0), (1, 0, 0), (0, 1, 0)),
    ((0, 0, 0), (1, 0, 0), (0, 0, 1)),
    ((0, 0, 0), (0, 1, 0), (0, 0, 1)),
    ((1, 0, 0), (0, 1, 0), (0, 0, 1)),
]


def _write_ascii_stl(path: Path) -> Path:
    lines = ["solid tet"]
    for tri in _TET_TRIS:
        lines.append("  facet normal 0 0 0")
        lines.append("    outer loop")
        for v in tri:
            lines.append(f"      vertex {v[0]} {v[1]} {v[2]}")
        lines.append("    endloop")
        lines.append("  endfacet")
    lines.append("endsolid tet")
    path.write_text("\n".join(lines))
    return path


def _write_binary_stl(path: Path, tris=_TET_TRIS) -> Path:
    blob = b"\x00" * 80 + struct.pack("<I", len(tris))
    for tri in tris:
        blob += struct.pack("<3f", 0, 0, 0)  # normal (recomputed by renderer)
        for v in tri:
            blob += struct.pack("<3f", *v)
        blob += struct.pack("<H", 0)
    path.write_bytes(blob)
    return path


def _write_obj(path: Path) -> Path:
    path.write_text(
        "v 0 0 0\nv 1 0 0\nv 1 1 0\nv 0 1 0\nv 0 0 1\n"
        "f 1 2 3 4\n"  # quad -> fan-triangulated into 2 triangles
        "f 1/1 2/2 5/5\n"  # v/vt style indices
    )
    return path


# --------------------------------------------------------------------------- #
# mesh loading
# --------------------------------------------------------------------------- #


def test_load_mesh_ascii_stl(tmp_path):
    tris = thumb.load_mesh(_write_ascii_stl(tmp_path / "tet.stl"))
    assert tris.shape == (4, 3, 3)
    assert tris.max() == 1.0 and tris.min() == 0.0


def test_load_mesh_binary_stl(tmp_path):
    tris = thumb.load_mesh(_write_binary_stl(tmp_path / "tet.stl"))
    assert tris.shape == (4, 3, 3)
    np.testing.assert_allclose(tris[0][1], (1, 0, 0))


def test_load_mesh_obj_triangulates_polygons(tmp_path):
    tris = thumb.load_mesh(_write_obj(tmp_path / "mesh.obj"))
    # 1 quad (2 tris) + 1 triangle = 3 triangles
    assert tris.shape == (3, 3, 3)


def test_load_mesh_unknown_format_raises(tmp_path):
    p = tmp_path / "mesh.xyz"
    p.write_text("nope")
    with pytest.raises(ValueError):
        thumb.load_mesh(p)


def test_load_mesh_missing_file_raises(tmp_path):
    with pytest.raises((ValueError, FileNotFoundError)):
        thumb.load_mesh(tmp_path / "absent.stl")


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #


def _png_size(path: Path) -> tuple[int, int]:
    from PIL import Image

    with Image.open(path) as im:
        return im.size


def test_render_thumbnail_writes_png(tmp_path):
    stl = _write_binary_stl(tmp_path / "tet.stl")
    out = thumb.render_thumbnail(stl, tmp_path / "out" / "thumbnail.png", size=128)
    assert out == tmp_path / "out" / "thumbnail.png"
    data = out.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert _png_size(out) == (128, 128)


def test_render_thumbnail_draws_something(tmp_path):
    from PIL import Image

    stl = _write_ascii_stl(tmp_path / "tet.stl")
    out = thumb.render_thumbnail(stl, tmp_path / "thumbnail.png")
    with Image.open(out) as im:
        alpha = np.asarray(im.convert("RGBA"))[..., 3]
    # a real model renders opaque pixels on a transparent background
    assert (alpha > 0).any() and (alpha == 0).any()


def test_render_thumbnail_empty_mesh_raises(tmp_path):
    empty = tmp_path / "empty.stl"
    empty.write_bytes(b"\x00" * 80 + struct.pack("<I", 0))
    with pytest.raises((ValueError, RuntimeError)):
        thumb.render_thumbnail(empty, tmp_path / "thumbnail.png")


# --------------------------------------------------------------------------- #
# multi-body meshes (#43)
# --------------------------------------------------------------------------- #
#
# mm-scale multi-body Compounds (piston assembly, butt hinge) bake to a single
# STL whose triangles form several disjoint solids. The renderer must fit its
# camera to the union bbox so every body lands in frame — a fit against a
# single body would push its siblings off-canvas.


def _cube_tris(center, half):
    """12 triangles of an axis-aligned cube (winding irrelevant: no culling)."""
    x, y, z = center
    v = [
        (x + sx * half, y + sy * half, z + sz * half)
        for sx in (-1, 1)
        for sy in (-1, 1)
        for sz in (-1, 1)
    ]
    quads = [
        (0, 1, 3, 2),
        (4, 6, 7, 5),  # -x / +x
        (0, 4, 5, 1),
        (2, 3, 7, 6),  # -y / +y
        (0, 2, 6, 4),
        (1, 5, 7, 3),  # -z / +z
    ]
    tris = []
    for a, b, c, d in quads:
        tris.append((v[a], v[b], v[c]))
        tris.append((v[a], v[c], v[d]))
    return tris


# Two 10 mm cubes 60 mm apart — a mm-scale two-body assembly stand-in.
_BODY_A = _cube_tris((0.0, 0.0, 0.0), 5.0)
_BODY_B = _cube_tris((60.0, 0.0, 0.0), 5.0)


def _projected_px(all_tris, body_tris, size):
    """Final-image pixel coords of body vertices, mirroring render_software."""
    basis = thumb._isometric_basis()
    xy_all = (np.asarray(all_tris, float).reshape(-1, 3) @ basis.T)[:, :2]
    lo, hi = xy_all.min(axis=0), xy_all.max(axis=0)
    extent = float(max((hi - lo).max(), 1e-9))
    canvas = size * thumb._SUPERSAMPLE
    scale = canvas * (1 - 2 * thumb._MARGIN) / extent
    pts = (np.asarray(body_tris, float).reshape(-1, 3) @ basis.T)[:, :2]
    pts = (pts - (lo + hi) / 2) * scale
    pts[:, 1] *= -1
    pts += canvas / 2
    return pts / thumb._SUPERSAMPLE


def _opaque_alpha(png_path: Path) -> np.ndarray:
    from PIL import Image

    with Image.open(png_path) as im:
        return np.asarray(im.convert("RGBA"))[..., 3] > 0


def test_load_mesh_multi_solid_ascii_stl(tmp_path):
    """ASCII STLs may carry several `solid` blocks — all must be loaded."""
    blocks = []
    for name, dx in (("a", 0.0), ("b", 3.0)):
        lines = [f"solid {name}"]
        for tri in _TET_TRIS:
            lines.append("  facet normal 0 0 0")
            lines.append("    outer loop")
            for v in tri:
                lines.append(f"      vertex {v[0] + dx} {v[1]} {v[2]}")
            lines.append("    endloop")
            lines.append("  endfacet")
        lines.append(f"endsolid {name}")
        blocks.append("\n".join(lines))
    p = tmp_path / "two.stl"
    p.write_text("\n".join(blocks))
    tris = thumb.load_mesh(p)
    assert tris.shape == (8, 3, 3)
    assert tris[..., 0].max() == 4.0  # second solid's offset survived


def test_render_thumbnail_multibody_all_bodies_in_frame(tmp_path):
    size = 192
    stl = _write_binary_stl(tmp_path / "assembly.stl", tris=_BODY_A + _BODY_B)
    out = thumb.render_thumbnail(stl, tmp_path / "thumbnail.png", size=size)
    opaque = _opaque_alpha(out)
    assert opaque.any()

    # camera fitted to the union bbox: nothing clips the canvas border
    assert not opaque[0].any() and not opaque[-1].any()
    assert not opaque[:, 0].any() and not opaque[:, -1].any()

    # the opaque footprint matches the union-projected bbox. A fit against a
    # single body instead of the union magnifies that body until it blankets
    # the frame — the per-body region checks below would then false-pass, but
    # this bbox comparison catches the skew.
    pad = 5  # LANCZOS ring + rounding
    all_tris = _BODY_A + _BODY_B
    px_all = _projected_px(all_tris, all_tris, size)
    ys, xs = np.where(opaque)
    assert abs(xs.min() - px_all[:, 0].min()) <= pad
    assert abs(xs.max() - px_all[:, 0].max()) <= pad
    assert abs(ys.min() - px_all[:, 1].min()) <= pad
    assert abs(ys.max() - px_all[:, 1].max()) <= pad

    # opaque pixels exist inside each body's own projected footprint
    for body in (_BODY_A, _BODY_B):
        px = _projected_px(_BODY_A + _BODY_B, body, size)
        x0, y0 = np.floor(px.min(axis=0)).astype(int)
        x1, y1 = np.ceil(px.max(axis=0)).astype(int)
        assert 0 <= x0 <= x1 < size and 0 <= y0 <= y1 < size
        assert opaque[y0 : y1 + 1, x0 : x1 + 1].any(), "body missing from frame"


def test_render_thumbnail_multibody_bodies_render_separately(tmp_path):
    """Both cubes appear as distinct blobs with background between them."""
    size = 192
    stl = _write_binary_stl(tmp_path / "assembly.stl", tris=_BODY_A + _BODY_B)
    out = thumb.render_thumbnail(stl, tmp_path / "thumbnail.png", size=size)
    opaque = _opaque_alpha(out)

    # LANCZOS downscale of the supersampled canvas rings a few pixels past
    # each blob's true edge — pad the projected bboxes before gap-checking.
    pad = 4
    px_a = _projected_px(_BODY_A + _BODY_B, _BODY_A, size)
    px_b = _projected_px(_BODY_A + _BODY_B, _BODY_B, size)
    a_right = int(np.ceil(px_a[:, 0].max())) + pad
    b_left = int(np.floor(px_b[:, 0].min())) - pad
    assert a_right < b_left  # fixtures project to disjoint x-ranges
    gap = opaque[:, a_right + 1 : b_left]
    assert gap.size > 0 and not gap.any()


def test_renderer_chain_falls_back(tmp_path, monkeypatch):
    """A failing (e.g. GL) renderer ahead of the software one is skipped."""
    calls = []

    def broken(tris, out_path, size):
        calls.append("broken")
        raise RuntimeError("no GL context")

    monkeypatch.setattr(thumb, "RENDERERS", [broken, *thumb.RENDERERS])
    out = thumb.render_thumbnail(
        _write_binary_stl(tmp_path / "tet.stl"), tmp_path / "thumbnail.png"
    )
    assert calls == ["broken"]
    assert out.exists() and out.read_bytes()[:4] == b"\x89PNG"


def test_renderer_chain_all_fail_raises(tmp_path, monkeypatch):
    def broken(tris, out_path, size):
        raise RuntimeError("boom")

    monkeypatch.setattr(thumb, "RENDERERS", [broken])
    with pytest.raises(RuntimeError):
        thumb.render_thumbnail(_write_binary_stl(tmp_path / "tet.stl"), tmp_path / "thumbnail.png")
