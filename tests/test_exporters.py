"""Exporter tests (STEP, glTF). Marked build123d."""

import os

import pytest

pytestmark = pytest.mark.build123d


@pytest.fixture
def box():
    from build123d import Box

    return Box(12, 8, 4)


def test_step_export_and_roundtrip(box, tmp_path):
    from build123d import import_step

    from cadless.exporters import export_step

    path = export_step(box, str(tmp_path))
    assert path.endswith(".step")
    assert os.path.getsize(path) > 0

    reimported = import_step(path)
    assert reimported.volume == pytest.approx(box.volume, rel=1e-3)


def test_glb_export_is_valid_binary_gltf(box, tmp_path):
    from cadless.exporters import export_glb

    path = export_glb(box, str(tmp_path))
    assert path.endswith(".glb")
    assert os.path.getsize(path) > 0
    with open(path, "rb") as fh:
        magic = fh.read(4)
    assert magic == b"glTF"  # binary glTF container magic


def test_glb_deflection_is_configurable(box, tmp_path):
    from cadless.exporters import export_glb

    coarse = export_glb(box, str(tmp_path), name="coarse", deflection=1.0)
    fine = export_glb(box, str(tmp_path), name="fine", deflection=0.01)
    assert os.path.getsize(coarse) > 0 and os.path.getsize(fine) > 0


def test_stl_export_is_valid_binary(box, tmp_path):
    import struct

    from cadless.exporters import export_stl

    path = export_stl(box, str(tmp_path))
    assert path.endswith(".stl")
    size = os.path.getsize(path)
    assert size > 84  # 80-byte header + 4-byte triangle count
    with open(path, "rb") as fh:
        count = struct.unpack("<I", fh.read(84)[80:84])[0]
    # binary STL is exactly header + count + 50 bytes per triangle
    assert size == 84 + 50 * count
    assert count == 12  # a box tessellates to 12 triangles


def test_obj_export_structure_matches_tessellation(box, tmp_path):
    from cadless.exporters import export_obj

    verts, tris = box.tessellate(0.1)
    path = export_obj(box, str(tmp_path))
    assert path.endswith(".obj")

    lines = open(path).read().splitlines()
    v_lines = [ln for ln in lines if ln.startswith("v ")]
    f_lines = [ln for ln in lines if ln.startswith("f ")]
    assert len(v_lines) == len(verts)
    assert len(f_lines) == len(tris) == 12
    # OBJ indices are 1-based and within range
    for ln in f_lines:
        idx = [int(tok) for tok in ln.split()[1:]]
        assert len(idx) == 3
        assert all(1 <= i <= len(verts) for i in idx)


def test_exporters_registry_covers_all_formats():
    from cadless.exporters import EXPORTERS

    assert set(EXPORTERS) == {"step", "glb", "stl", "obj"}
