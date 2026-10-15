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


# --- where a located shape is drawn ---------------------------------------
#
# A shape carries a Location of its own: algebra placement gives it one, moving
# a part gives it one, and so does every solid split out of a multi-solid
# result. The GLB has to draw each of them where the shape is, or the parts of
# one build are drawn out of place relative to each other.


def _located_solid():
    from build123d import Box, Pos

    return Pos(0, 70, 50) * Box(10, 20, 30)


def _located_part():
    # Turned as well as moved: a location's rotation has to reach the drawing
    # too, not only its translation.
    from build123d import Box, BuildPart, Pos, Rot

    with BuildPart() as built:
        Box(10, 20, 30)
    return Pos(0, 70, 50) * Rot(0, 0, 30) * built.part


def _located_shell():
    from build123d import Box, Pos, Shell

    return Pos(0, 70, 50) * Shell(Box(10, 20, 30).faces())


def _moved_compound_holding_a_moved_child():
    # Two locations in a chain: the child's inside the compound, the compound's
    # own outside it. Both have to be composed, in that order.
    from build123d import Box, Compound, Pos

    return Compound(children=[Pos(100, 0, 0) * Box(10, 20, 30)]).moved(Pos(0, 70, 50))


@pytest.mark.parametrize(
    "build",
    [_located_solid, _located_part, _located_shell, _moved_compound_holding_a_moved_child],
    ids=["solid", "part", "shell", "moved-compound"],
)
def test_a_located_shape_is_drawn_where_it_is(build, tmp_path):
    """The GLB draws the shape inside its own bounding box, read in the CAD frame.

    Read through every node transform down to the mesh, as a viewer reads it, so
    a shape whose placement is written onto the wrong side of the file's Z-up to
    Y-up turn is caught however the file nests it.
    """
    from cadless.exporters import export_glb
    from tests.glb_bounds import PLACEMENT_TOLERANCE_MM, glb_world_bounds

    # Tessellation puts a box's vertices on its corners, so the drawn bounds
    # of every shape here are its bounding box, not an approximation of it.
    shape = build()
    expected = shape.bounding_box()
    low, high = glb_world_bounds(export_glb(shape, str(tmp_path)))
    assert [*low, *high] == pytest.approx(
        [*expected.min, *expected.max], abs=PLACEMENT_TOLERANCE_MM
    )


def _drawn_names_and_colours(path):
    """Each drawn node's name with the base colours of the materials it is drawn in."""
    from tests.glb_bounds import read_glb

    document, _ = read_glb(path)
    materials = document.get("materials", [])
    drawn = set()
    for node in document["nodes"]:
        if "mesh" not in node:
            continue
        for primitive in document["meshes"][node["mesh"]]["primitives"]:
            # A primitive with no material is drawn in the viewer's default,
            # which is a colour lost, so it is recorded rather than skipped.
            index = primitive.get("material")
            colour = (
                None
                if index is None
                else tuple(materials[index]["pbrMetallicRoughness"]["baseColorFactor"])
            )
            drawn.add((node.get("name"), colour))
    return drawn


def test_a_located_part_keeps_its_name_and_colour(tmp_path):
    """Placing it right must not cost the part what it is called or how it looks.

    The writer files a shape's label as its node name and its colour as its
    material, so both are compared with what the writer gives the same shape
    when handed it directly -- where they are right, and only the place is not.
    """
    from build123d import Color, export_gltf

    from cadless.exporters import export_glb

    shape = _located_solid()
    shape.label = "bracket"
    shape.color = Color(0.8, 0.1, 0.1)
    ours = export_glb(shape, str(tmp_path), name="ours")
    direct = str(tmp_path / "direct.glb")
    export_gltf(shape, direct, binary=True)
    drawn = _drawn_names_and_colours(ours)
    assert drawn == _drawn_names_and_colours(direct)
    # Not a comparison of two empty sets: the label is there to be lost.
    assert {name for name, _ in drawn} == {"bracket"}


def test_a_part_exported_out_of_an_assembly_stays_in_it(tmp_path):
    """Exporting one part of an assembly does not take it out of the assembly.

    A shape adopted as another compound's child leaves the compound it was in,
    and that compound loses its geometry with it -- so the export must never be
    the thing that adopts the caller's own object.
    """
    from build123d import Box, Compound, Pos

    from cadless.exporters import export_glb

    first = Pos(100, 0, 0) * Box(10, 20, 30)
    second = Pos(0, 70, 50) * Box(10, 20, 30)
    assembly = Compound(children=[first, second])
    export_glb(second, str(tmp_path))
    assert second.parent is assembly


def test_exporting_a_located_shape_leaves_it_editable(tmp_path):
    """The caller's shape can still be built on after it has been exported.

    OCCT freezes a shape once it is put into a compound, and from then on its
    builder refuses to add anything to it or take anything out. A copy that
    shares the caller's underlying shape would freeze the caller's along with it.
    """
    from cadless.exporters import export_glb

    shape = _located_solid()
    export_glb(shape, str(tmp_path))
    assert shape.wrapped.Free()


def test_a_shape_with_no_location_of_its_own_is_written_as_before(tmp_path):
    """A shape already at the identity is handed to the writer untouched.

    Byte for byte the file a direct export writes. Most builds are such a shape,
    and adding a level of nesting to every one of them would change every file
    anything downstream already reads, to fix a placement that was right.
    """
    from pathlib import Path

    from build123d import Box, Color, export_gltf

    from cadless.exporters import export_glb

    shape = Box(10, 20, 30)
    shape.label = "bracket"
    shape.color = Color(0.8, 0.1, 0.1)
    ours = export_glb(shape, str(tmp_path), name="ours", deflection=0.1)
    direct = tmp_path / "direct.glb"
    export_gltf(shape, str(direct), binary=True, linear_deflection=0.1)
    assert Path(ours).read_bytes() == direct.read_bytes()
