"""Execution-worker tests. Marked build123d: spawns subprocesses that
import the OCCT kernel."""

from pathlib import Path

import pytest

from cadless.config import Settings
from cadless.worker import run_code

pytestmark = pytest.mark.build123d


def test_executes_valid_code_and_reports_geometry():
    res = run_code("from build123d import *\nresult = Box(10, 20, 30)")
    assert res.ok, res.error
    assert res.volume == pytest.approx(10 * 20 * 30, rel=1e-3)
    assert res.bbox == pytest.approx((10, 20, 30), rel=1e-3)
    # Deterministic assertion metrics: a single closed box.
    assert res.part_count == 1
    assert res.manifold is True


def test_reports_part_count_for_multiple_solids():
    res = run_code(
        "from build123d import *\nresult = Box(10, 10, 10) + Pos(30, 0, 0) * Box(5, 5, 5)\n"
    )
    assert res.ok, res.error
    assert res.part_count == 2


def test_runtime_exception_is_captured():
    res = run_code("from build123d import *\nresult = Box(1, 1, 1) - Box(1, 1, 1)\nresult.volume")
    # subtracting equal boxes -> empty/degenerate solid is rejected
    assert not res.ok
    assert res.error


def test_missing_result_is_error():
    res = run_code("from build123d import *\nx = Box(1, 1, 1)")
    assert not res.ok
    assert "result" in res.error


def test_syntax_or_runtime_error_text():
    res = run_code("from build123d import *\nresult = 1 / 0")
    assert not res.ok
    assert "ZeroDivisionError" in res.error


def test_runtime_error_yields_structured_repair_context():
    # A pure-Python error (no OCCT needed) on a known line of the generated script.
    code = "x = 1\ny = 2\nresult = x / 0\n"
    res = run_code(code)
    assert not res.ok
    ctx = res.repair_context
    assert ctx is not None
    assert ctx.error_type == "ZeroDivisionError"
    assert "division by zero" in ctx.message
    # The offending line is mapped back from the traceback to the generated script.
    assert ctx.offending_line == "result = x / 0"
    # Full traceback captured (not just the message).
    assert "Traceback (most recent call last)" in ctx.last_traceback
    assert "ZeroDivisionError" in ctx.last_traceback


def test_wall_clock_timeout():
    cfg = Settings(exec_timeout_secs=2.0)
    res = run_code("while True:\n    pass\nresult = 1", config=cfg)
    assert not res.ok
    assert res.timed_out


def test_export_writes_artifacts(tmp_path):
    res = run_code(
        "from build123d import *\nresult = Box(8, 8, 8)",
        export_dir=str(tmp_path),
    )
    assert res.ok, res.error
    assert res.step_path and res.glb_path
    import os

    assert os.path.getsize(res.step_path) > 0
    assert os.path.getsize(res.glb_path) > 0


def test_export_writes_one_file_per_part(tmp_path):
    """Two disjoint solids become two files of each kind, numbered from zero.

    The count was already reported before this; the geometry was not. Both solids
    went into one file per kind, so the second part had nowhere to be addressed
    from and nothing downstream could tell it had been lost.
    """
    res = run_code(
        "from build123d import *\nresult = Box(10, 10, 10) + Pos(30, 0, 0) * Box(5, 5, 5)\n",
        export_dir=str(tmp_path),
    )
    assert res.ok, res.error
    assert res.part_count == 2
    for kind in ("step", "stl"):
        assert sorted(p.name for p in tmp_path.glob(f"model_p*.{kind}")) == [
            f"model_p0.{kind}",
            f"model_p1.{kind}",
        ]
        # Not beside them: one shape per directory, or a reader finds both.
        assert not (tmp_path / f"model.{kind}").exists()


def test_each_part_of_a_split_build_is_drawn_where_it_is(tmp_path):
    """Every part's files span the box the part occupies in the built model.

    Both boxes sit where the ``Locations`` put them, and each part's GLB agrees
    with the OBJ written beside it: a GLB that disagrees draws that part away
    from its neighbours.
    """
    from cadless.catalog.thumbnail import load_mesh
    from tests.glb_bounds import PLACEMENT_TOLERANCE_MM, glb_world_bounds

    res = run_code(
        "from build123d import *\n"
        "with BuildPart() as built:\n"
        "    with Locations((100, 0, 0), (0, 70, 50)):\n"
        "        Box(10, 20, 30)\n"
        "result = built.part\n",
        export_dir=str(tmp_path),
    )
    assert res.ok, res.error
    expected = {0: [-5, 60, 35, 5, 80, 65], 1: [95, -10, -15, 105, 10, 15]}
    for index in (0, 1):
        vertices = load_mesh(tmp_path / f"model_p{index}.obj").reshape(-1, 3)
        obj_box = [*vertices.min(axis=0), *vertices.max(axis=0)]
        assert obj_box == pytest.approx(expected[index], abs=PLACEMENT_TOLERANCE_MM), index
        low, high = glb_world_bounds(tmp_path / f"model_p{index}.glb")
        assert [*low, *high] == pytest.approx(obj_box, abs=PLACEMENT_TOLERANCE_MM), f"part {index}"


_CAR = (
    "from build123d import *\n"
    "chassis = Box(30, 40, 4)\n"
    "chassis.label = 'chassis'\n"
    "axle = Box(20, 1, 1)\n"
    "axle.label = 'axle_f'\n"
    "wheel_fl = Pos(-10, 0, 0) * Cylinder(3, 2)\n"
    "wheel_fl.label = 'wheel_fl'\n"
    "wheel_fr = Pos(10, 0, 0) * Cylinder(3, 2)\n"
    "wheel_fr.label = 'wheel_fr'\n"
    "front = Compound(children=[axle, wheel_fl, wheel_fr], label='front_axle')\n"
    "front.location = Pos(0, 25, 0)\n"
    "result = Compound(children=[chassis, front], label='car')\n"
)


def test_a_labelled_assembly_reports_its_parts_by_name_in_solids_order(tmp_path):
    """The names the code gave its parts come back with the build."""
    res = run_code(_CAR, export_dir=str(tmp_path))

    assert res.ok, res.error
    assert res.part_count == 4
    assert [(p["index"], p["name"], p["path"]) for p in res.parts] == [
        (0, "chassis", ["car"]),
        (1, "axle_f", ["car", "front_axle"]),
        (2, "wheel_fl", ["car", "front_axle"]),
        (3, "wheel_fr", ["car", "front_axle"]),
    ]
    assert sorted(p.name for p in tmp_path.glob("model_p*.stl")) == [
        f"model_p{i}.stl" for i in range(4)
    ]


def test_a_one_solid_build_reports_one_unnamed_part():
    res = run_code("from build123d import *\nresult = Box(8, 8, 8)")

    assert res.ok, res.error
    assert res.parts == [{"index": 0, "name": "part0", "path": [], "color": None}]


def test_a_one_solid_build_exports_the_shape_it_was_given(tmp_path):
    """A one-part build's files are byte for byte what exporting the shape itself writes.

    The part read out of the tree is the same geometry with its placement baked
    in, and its GLB would be written differently: the shape the code built is
    what a one-part build has always exported.
    """
    from build123d import Box, Pos, Rot

    from cadless import exporters

    code = "from build123d import *\nresult = Pos(5, 0, 0) * Rot(0, 0, 30) * Box(4, 6, 8)\n"
    res = run_code(code, export_dir=str(tmp_path / "built"))
    assert res.ok, res.error

    shape = Pos(5, 0, 0) * Rot(0, 0, 30) * Box(4, 6, 8)
    for kind in ("glb", "stl", "obj"):
        direct = exporters.EXPORTERS[kind](shape, str(tmp_path / "direct"), "model")
        built = tmp_path / "built" / f"model.{kind}"
        assert built.read_bytes() == Path(direct).read_bytes(), kind


def _built_boxes(code: str, scale: float) -> list[list[float]]:
    """Each solid's box in the built model, in millimetres at ``scale``."""
    namespace: dict = {}
    exec(code, namespace)  # noqa: S102 - the test's own fixed code
    boxes = []
    for solid in namespace["result"].solids():
        bb = solid.bounding_box()
        boxes.append([scale * v for v in (*bb.min, *bb.max)])
    return boxes


def _obj_box(path) -> list[float]:
    from cadless.catalog.thumbnail import load_mesh

    vertices = load_mesh(path).reshape(-1, 3)
    return [*vertices.min(axis=0), *vertices.max(axis=0)]


def test_a_placed_assembly_is_scaled_into_millimetres_part_by_part(tmp_path):
    """The export scale converts a part's position as well as its size.

    The car here is placed 100 units out, and every part file lands at 2x its
    place in the model, each under the file number its name was reported with.
    """
    code = _CAR + "result.location = Pos(100, 0, 0)\n"

    res = run_code(code, export_dir=str(tmp_path), export_scale=2.0)

    assert res.ok, res.error
    assert [p["name"] for p in res.parts] == ["chassis", "axle_f", "wheel_fl", "wheel_fr"]
    for index, expected in enumerate(_built_boxes(code, 2.0)):
        # A cylinder's mesh falls short of its exact box by its tessellation.
        assert _obj_box(tmp_path / f"model_p{index}.obj") == pytest.approx(expected, abs=0.01)


def test_a_placed_single_solid_is_scaled_about_the_origin(tmp_path):
    """A one-part build is exported as the shape it was, converted the same way as parts."""
    code = "from build123d import *\nresult = Pos(5, 0, 0) * Rot(0, 0, 30) * Box(4, 6, 8)\n"

    res = run_code(code, export_dir=str(tmp_path), export_scale=1000.0)

    assert res.ok, res.error
    (expected,) = _built_boxes(code, 1000.0)
    assert _obj_box(tmp_path / "model.obj") == pytest.approx(expected, abs=1e-2)


def test_a_single_solid_still_exports_under_the_plain_name(tmp_path):
    """The upgrade path. An installation that never asks for an assembly sees
    exactly the tree it has always had, which is what lets the re-run path go on
    looking for ``model.{kind}`` by name."""
    res = run_code("from build123d import *\nresult = Box(8, 8, 8)", export_dir=str(tmp_path))
    assert res.ok, res.error
    assert (tmp_path / "model.step").exists()
    assert list(tmp_path.glob("model_p*.step")) == []


def test_a_rebuild_leaves_none_of_the_previous_shape_behind(tmp_path):
    """Callers reuse an export directory, so a build lands on top of the last
    one's files. Without clearing, a two-part build followed by a one-solid one
    leaves both namings side by side and whoever scans the directory has to guess
    which is current -- and the stale one wins whenever it is found first.
    """
    run_code(
        "from build123d import *\nresult = Box(10, 10, 10) + Pos(30, 0, 0) * Box(5, 5, 5)\n",
        export_dir=str(tmp_path),
    )
    assert list(tmp_path.glob("model_p*.stl"))

    run_code("from build123d import *\nresult = Box(8, 8, 8)", export_dir=str(tmp_path))

    assert (tmp_path / "model.stl").exists()
    assert list(tmp_path.glob("model_p*.stl")) == []


def test_an_assembly_is_also_written_whole(tmp_path):
    """A build of several parts gets one GLB and one STEP of the whole, beside its parts."""
    res = run_code(_CAR, export_dir=str(tmp_path))

    assert res.ok, res.error
    assert res.assembly_glb_path == str(tmp_path / "assembly.glb")
    assert res.assembly_step_path == str(tmp_path / "assembly.step")
    assert (tmp_path / "assembly.glb").stat().st_size > 0
    assert (tmp_path / "assembly.step").stat().st_size > 0


def test_a_one_part_rebuild_leaves_no_whole_assembly_behind(tmp_path):
    """The whole-assembly files go with the build that wrote them, and the motion
    file beside them -- the pipeline's, not the worker's -- is left alone."""
    run_code(_CAR, export_dir=str(tmp_path))
    assert (tmp_path / "assembly.glb").exists()
    (tmp_path / "assembly.json").write_text("{}")

    res = run_code("from build123d import *\nresult = Box(8, 8, 8)", export_dir=str(tmp_path))

    assert res.ok, res.error
    assert res.assembly_glb_path is None
    assert not (tmp_path / "assembly.glb").exists()
    assert not (tmp_path / "assembly.step").exists()
    assert (tmp_path / "assembly.json").read_text() == "{}"


def test_export_scale_scales_artifacts_not_geometry(tmp_path):
    """Metre-authored domains export at 1000x so mm-assuming consumers read
    correct real-world size (issue #18); the geometry summary stays unscaled."""
    res = run_code(
        "from build123d import *\nresult = Box(2, 2, 2)",
        export_dir=str(tmp_path),
        export_scale=1000.0,
    )
    assert res.ok, res.error
    assert res.volume == pytest.approx(8.0, rel=1e-3)  # authoring units
    assert res.bbox == pytest.approx((2, 2, 2), rel=1e-3)

    # The thumbnail renderer reads meshes on the reading side; the metrics
    # reader this used to call left with the authoring stack.
    from cadless.catalog.thumbnail import load_mesh

    tris = load_mesh(res.stl_path).reshape(-1, 3)
    span = tris.max(axis=0) - tris.min(axis=0)
    assert span == pytest.approx((2000.0, 2000.0, 2000.0), rel=1e-3)


# --- the assembly measurement, through the real child ---------------------


def test_a_multi_part_build_reports_assembly_measurements_when_asked():
    res = run_code(
        "from build123d import *\nresult = Box(20, 20, 10) + Pos(20.2, 0, 0) * Box(20, 20, 10)\n",
        check_assembly=True,
    )
    assert res.ok, res.error
    assert res.part_count == 2
    assert res.assembly is not None
    assert len(res.assembly.part_bboxes) == 2
    assert res.assembly.order is not None


def test_a_build_that_did_not_ask_carries_no_assembly_measurements():
    res = run_code(
        "from build123d import *\nresult = Box(20, 20, 10) + Pos(20.2, 0, 0) * Box(20, 20, 10)\n"
    )
    assert res.ok, res.error
    assert res.part_count == 2
    assert res.assembly is None


def test_a_single_part_build_carries_no_assembly_measurements_even_when_asked():
    res = run_code("from build123d import *\nresult = Box(10, 20, 30)", check_assembly=True)
    assert res.ok, res.error
    assert res.assembly is None


def test_the_order_search_reports_the_axis_each_part_comes_out_along():
    # A tongue inside a through-channel: the only way either part comes free is
    # along the channel. The channel deliberately runs in Z rather than X, because
    # X is the first direction the search tries -- a search that recorded the
    # candidate it started with instead of the one that worked would pass a
    # channel in X and has to go red here.
    res = run_code(
        "from build123d import *\n"
        "result = (Box(20, 20, 40) - Box(10.4, 10.4, 41)) + Box(10, 10, 39.6)\n",
        check_assembly=True,
    )
    assert res.ok, res.error
    assert res.part_count == 2
    assert res.assembly is not None
    assert res.assembly.order is not None
    assert len(res.assembly.releases) == 2
    # One entry per part, and the part left standing carries none: nothing is
    # left to block it, so any heading would pass and none would be measured.
    recorded = [release for release in res.assembly.releases if release]
    assert len(recorded) == 1
    for release in recorded:
        assert abs(release[2]) == pytest.approx(1.0, abs=1e-6)
        assert release[0] == pytest.approx(0.0, abs=1e-6)
        assert release[1] == pytest.approx(0.0, abs=1e-6)


def test_the_headings_are_filed_by_part_and_not_by_the_order_they_were_freed_in():
    # Everything downstream indexes releases by part, and filing them by the
    # order parts came out instead would still give one entry each, one of them
    # empty, every heading a real one -- with each part drawn moving the way a
    # different part moves.
    #
    # A stack cannot show that: the search frees the lowest free index first, so
    # on anything unobstructed the two orders coincide and the mistake is
    # invisible. Here a peg sits in a blind hole under a lid and cannot move at
    # all until the block housing it drops away, so the block comes out first and
    # the orders disagree. The peg then slides out sideways, which is a different
    # heading from the block's -- filed the other way round, the two swap.
    res = run_code(
        "from build123d import *\n"
        "peg = Pos(0, 0, 5.0) * Box(10, 10, 9.6)\n"
        "block = Box(40, 40, 20) - Pos(0, 0, 5.5) * Box(10.4, 10.4, 11)\n"
        "lid = Pos(0, 0, 13.2) * Box(40, 40, 6)\n"
        "result = peg + block + lid\n",
        check_assembly=True,
    )
    assert res.ok, res.error
    assert res.assembly is not None
    order = res.assembly.order
    assert order is not None
    boxes = res.assembly.part_bboxes
    releases = res.assembly.releases
    assert len(releases) == 3

    # The premise, asserted rather than assumed: this catches the mistake only
    # while the two orders disagree, and which index each solid lands on is the
    # kernel's to decide. Left unstated, a kernel that returned them in another
    # order would line the two up and the test would go quietly green while
    # guarding nothing -- which is what the first version of this fixture did.
    removal = list(reversed(order))
    assert removal[:-1] != list(range(len(removal) - 1)), (
        f"fixture no longer discriminates: removal order {removal} follows part order"
    )

    # Identified by shape rather than by a fixed index, so the test reads the
    # same whatever order the solids come back in.
    peg = min(range(3), key=lambda i: boxes[i][0] * boxes[i][1])
    block = max(range(3), key=lambda i: boxes[i][2])
    assert peg != block

    assert releases[block][2] == pytest.approx(-1.0, abs=1e-6)  # drops away downward
    assert abs(releases[peg][0]) == pytest.approx(1.0, abs=1e-6)  # slides out sideways
    assert releases[order[0]] == []  # the part left standing carries no heading


def test_the_parts_are_measured_after_the_export_scale_is_applied():
    # The summary stays in authoring units while the export is millimetres. The
    # build volume the checks run against is millimetres, so measuring the
    # unscaled solids would compare the two and never refuse a metres-authored
    # model. The bbox below is 1 unit; scaled by 1000 the part boxes must read
    # 1000 mm while the summary bbox still reads 1.
    res = run_code(
        "from build123d import *\nresult = Box(1, 1, 1) + Pos(2, 0, 0) * Box(1, 1, 1)\n",
        export_scale=1000.0,
        check_assembly=True,
    )
    assert res.ok, res.error
    assert res.bbox == pytest.approx((3.0, 1.0, 1.0), rel=1e-3)
    assert res.assembly is not None
    for box in res.assembly.part_bboxes:
        assert box == pytest.approx([1000.0, 1000.0, 1000.0], rel=1e-3)


def test_labels_that_are_not_text_still_name_the_whole_assembly(tmp_path):
    code = (
        "from build123d import *\n"
        "a = Box(4, 4, 4)\n"
        "b = Pos(10, 0, 0) * Box(4, 4, 4)\n"
        "result = Compound(children=[a, b])\n"
        "a.label = 1\n"
        "b.label = 2\n"
    )

    res = run_code(code, export_dir=str(tmp_path))

    assert res.ok, res.error
    assert [p["name"] for p in res.parts] == ["1", "2"]
    assert (tmp_path / "assembly.glb").stat().st_size > 0


def test_a_dangling_link_where_a_whole_file_goes_is_cleared(tmp_path):
    """``exists`` follows a link, so a dangling one would be written through."""
    target = tmp_path / "elsewhere.step"
    (tmp_path / "assembly.step").symlink_to(target)

    res = run_code(_CAR, export_dir=str(tmp_path))

    assert res.ok, res.error
    assert not (tmp_path / "assembly.step").is_symlink()
    assert not target.exists()


def test_a_dangling_link_where_a_part_file_goes_is_cleared(tmp_path):
    target = tmp_path / "elsewhere.stl"
    (tmp_path / "model_p0.stl").symlink_to(target)

    res = run_code(_CAR, export_dir=str(tmp_path))

    assert res.ok, res.error
    assert not (tmp_path / "model_p0.stl").is_symlink()
    assert not target.exists()
