"""Execution-worker tests. Marked build123d: spawns subprocesses that
import the OCCT kernel."""

import json
import math
import subprocess
import sys
import types
from pathlib import Path

import pytest

from cadless import worker
from cadless._worker_child import SENTINEL
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


def test_a_build_on_an_assembly_turn_runs_on_the_assembly_clock():
    cfg = Settings(exec_timeout_secs=1.0, assembly_exec_timeout_secs=2.0)
    res = run_code(
        "while True:\n    pass\nresult = 1", check_assembly=True, assembly_turn=True, config=cfg
    )
    assert res.timed_out
    assert "exceeded 2s" in res.error


def test_a_measured_build_on_any_other_turn_keeps_the_plain_clock():
    # Every pipeline build is measured, so the measurement cannot be what earns
    # the longer clock: only a turn that asked for an assembly does.
    cfg = Settings(exec_timeout_secs=1.0, assembly_exec_timeout_secs=2.0)
    res = run_code("while True:\n    pass\nresult = 1", check_assembly=True, config=cfg)
    assert res.timed_out
    assert "exceeded 1s" in res.error


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


def test_where_parts_overlap_comes_back_in_the_units_the_script_wrote():
    # Measured in millimetres, a script written in metres is still told where its
    # parts meet in the coordinates it placed them at.
    res = run_code(
        "from build123d import *\n"
        "result = Compound(children=[Box(1, 1, 1), Pos(0.5, 0, 0) * Box(1, 1, 1)])\n",
        export_scale=1000.0,
        check_assembly=True,
    )
    assert res.ok, res.error
    assert res.assembly is not None
    _first, _second, *box = res.assembly.overlap_boxes[0]
    assert box == pytest.approx([0.0, -0.5, -0.5, 0.5, 0.5, 0.5], abs=1e-6)


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


def test_the_measurement_takes_a_share_of_the_time_left():
    """A build that already ran long is not pushed past its own timeout."""
    from cadless import assembly_measure
    from cadless._worker_child import measurement_budget

    share = assembly_measure.MEASUREMENT_TIME_SHARE
    assert measurement_budget(30.0, 0.0) == pytest.approx(30.0 * share)
    assert measurement_budget(30.0, 27.0) == pytest.approx(3.0 * share)
    assert measurement_budget(30.0, 40.0) == 0.0


def test_a_worker_with_no_clock_measures_on_the_fixed_budget():
    from cadless import assembly_measure
    from cadless._worker_child import measurement_budget

    assert measurement_budget(0.0, 5.0) == assembly_measure.MEASUREMENT_TIME_BUDGET_SECONDS


def test_the_child_measures_on_a_share_of_the_time_left(tmp_path, monkeypatch, capsys):
    """Wired through the child itself, not only the helper beside it."""
    import time

    from cadless import _worker_child, assembly_measure
    from cadless.assembly_check import AssemblyMeasurements

    seen = {}

    def fake_measure(parts, time_budget, names=None, scale=1.0):
        seen["budget"] = time_budget
        return AssemblyMeasurements(part_bboxes=[], gaps=[], order=None)

    monkeypatch.setattr(assembly_measure, "measure_assembly", fake_measure)
    monkeypatch.setattr(_worker_child, "_STARTED", time.monotonic() - 12.0)
    code_file = tmp_path / "m.py"
    code_file.write_text(
        "from build123d import *\nresult = Compound([Box(1, 1, 1), Pos(5, 0, 0) * Box(1, 1, 1)])\n"
    )

    _worker_child.main(["prog", str(code_file), "", "1", "1", "30"])
    capsys.readouterr()

    assert 0.0 < seen["budget"] <= (30.0 - 12.0) * assembly_measure.MEASUREMENT_TIME_SHARE


def test_the_child_hands_the_tree_s_names_to_the_measurement(tmp_path, monkeypatch, capsys):
    from cadless import _worker_child, assembly_measure
    from cadless.assembly_check import AssemblyMeasurements

    seen = {}

    def fake_measure(parts, time_budget, names=None, scale=1.0):
        seen["names"] = names
        return AssemblyMeasurements(part_bboxes=[], gaps=[], order=None)

    monkeypatch.setattr(assembly_measure, "measure_assembly", fake_measure)
    code_file = tmp_path / "m.py"
    code_file.write_text(
        "from build123d import *\n"
        "a = Box(1, 1, 1)\n"
        "a.label = 'base'\n"
        "b = Pos(5, 0, 0) * Box(1, 1, 1)\n"
        "b.label = 'lid'\n"
        "result = Compound(children=[a, b])\n"
    )

    _worker_child.main(["prog", str(code_file), "", "1", "1", "30"])
    capsys.readouterr()

    assert seen["names"] == ["base", "lid"]


# --- what the parent reads back from the child -------------------------------
#
# A worker on another engine build, or a child that went wrong, can send a line
# of any shape: nothing in it can be trusted to have the shape this child would
# have given it.


def _forged(monkeypatch, payload, returncode: int = 0) -> None:
    """Have the next child run print ``payload`` as its result line and exit with ``returncode``."""
    line = f"{SENTINEL} {payload if isinstance(payload, str) else json.dumps(payload)}"

    def run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, returncode, stdout=line + "\n", stderr="")

    stand_in = types.SimpleNamespace(run=run, TimeoutExpired=subprocess.TimeoutExpired)
    monkeypatch.setattr(worker, "subprocess", stand_in)


@pytest.mark.parametrize(
    "line",
    ["[1, 2]", "[" * 100_000 + "]" * 100_000, '{"ok": true, "volume": ' + "1" * 5_000 + "}"],
    ids=["a list", "nesting past the parser", "an integer past the parser"],
)
def test_a_result_that_is_not_an_object_is_a_failed_build(monkeypatch, line):
    # The last two raised out of the parser, and so out of run_code.
    _forged(monkeypatch, line)
    res = run_code("result = 1\n")
    assert res.ok is False
    assert res.error


@pytest.mark.parametrize("returncode", [1, -9], ids=["an error", "a signal"])
def test_a_success_from_a_child_that_did_not_exit_cleanly_is_a_failed_build(
    monkeypatch, returncode
):
    # A child that reported success exits 0. One that did not finish cleanly
    # after its line is no success, whatever the line says; one a signal ended
    # is reported as the no-line case reports it.
    _forged(monkeypatch, {"ok": True, "volume": 1.0, "bbox": [1.0, 1.0, 1.0]}, returncode)

    res = run_code("result = 1\n")

    assert (res.ok, res.timed_out) == (False, returncode < 0)


@pytest.mark.parametrize(
    ("field", "sent"),
    [
        ("part_count", "3"),
        ("part_count", True),
        ("part_count", -1),
        ("manifold", "no"),
        ("min_wall_thickness", math.inf),
        ("min_wall_thickness", -1.0),
        ("step_path", 5),
    ],
    ids=[
        "text for a count",
        "a flag for a count",
        "a count below zero",
        "text for a flag",
        "a thickness that is no number",
        "a thickness below zero",
        "a number for a path",
    ],
)
def test_a_value_that_is_no_reading_comes_back_as_none(monkeypatch, field, sent):
    # Each was handed on as it came, and every reader downstream took it for a
    # count, a flag, a measurement or a path.
    _forged(monkeypatch, {"ok": True, "volume": 1.0, "bbox": [1.0, 1.0, 1.0], field: sent})
    res = run_code("result = 1\n")
    assert res.ok is True
    assert getattr(res, field) is None


@pytest.mark.parametrize(
    "result",
    [
        {"volume": 1.0, "bbox": 5},
        {"volume": 1.0, "bbox": "abc"},
        {"volume": 1.0, "bbox": [math.nan, 1.0, 1.0]},
        {"volume": 1.0},
        {"volume": math.nan, "bbox": [1.0, 1.0, 1.0]},
        {"volume": -1.0, "bbox": [1.0, 1.0, 1.0]},
        {"bbox": [1.0, 1.0, 1.0]},
    ],
    ids=[
        "a number for the box",
        "text for the box",
        "a box that is no size",
        "no box",
        "a volume that is no number",
        "a volume below zero",
        "no volume",
    ],
)
def test_a_success_with_no_volume_or_box_it_can_read_is_a_failed_build(monkeypatch, result):
    # Every success the child reports carries both. Handed on, the first raised
    # out of the reader, the second read a letter to a side, and the rest passed
    # as successes with a reading nobody took, or with none at all.
    _forged(monkeypatch, {"ok": True, **result})
    res = run_code("result = 1\n")
    assert res.ok is False


def test_a_lone_part_whose_box_cannot_be_read_is_refused_on_a_turn_held_to_the_bed(monkeypatch):
    # Read as a box not measured, a lone part would be handed over without being
    # held to the bed, so a success whose box cannot be read is no success.
    from cadless.pipeline import rebuild
    from cadless.printer_profile import AssemblySpec, BuildVolume

    spec = AssemblySpec(volume=BuildVolume(210.0, 200.0, 195.0), clearance_mm=0.2)
    sent = {"ok": True, "volume": 1.0, "bbox": [math.nan, 1.0, 1.0], "part_count": 1}
    _forged(monkeypatch, sent)
    assert rebuild("result = 1\n", assembly=spec).ok is False


@pytest.mark.parametrize(
    "parts",
    [
        7,
        [7],
        [{"name": "lid"}],
        [{"index": 0}],
        [{"index": 0, "name": "lid", "path": "car"}],
        [{"index": 0, "name": "lid", "path": [1]}],
        [{"index": 0, "name": "lid", "path": [], "color": "red"}],
        [{"index": 0, "name": "lid", "path": [], "color": ["r", 0.0, 0.0, 1.0]}],
    ],
    ids=[
        "a number for the list",
        "a number for a part",
        "a part with no index",
        "a part with no name",
        "text for a path",
        "a number in a path",
        "text for a colour",
        "text in a colour",
    ],
)
def test_parts_the_store_would_not_keep_come_back_as_none(monkeypatch, parts):
    # Read by the rule the store files them by, so what a run hands the turn is
    # the list its version will be filed with.
    _forged(monkeypatch, {"ok": True, "volume": 1.0, "bbox": [1.0, 1.0, 1.0], "parts": parts})
    res = run_code("result = 1\n")
    assert res.parts is None


def test_a_traceback_that_is_not_text_is_left_out_of_the_repair_context(monkeypatch):
    # The repair prompt strips it as text, and anything else raised there.
    _forged(
        monkeypatch, {"ok": False, "error": "boom", "error_type": "ValueError", "traceback": [1]}
    )
    res = run_code("result = 1\n")
    assert res.repair_context.error_type == "ValueError"
    assert res.repair_context.last_traceback is None


def test_an_error_type_that_is_not_text_is_no_repair_context(monkeypatch):
    _forged(monkeypatch, {"ok": False, "error": "boom", "error_type": 5})
    res = run_code("result = 1\n")
    assert res.repair_context is None


# --- what the child refuses to report as a build -----------------------------


def _child(tmp_path, capsys, code: str, *args: str) -> dict:
    """The payload the child prints for ``code``, run in this process."""
    from cadless import _worker_child

    code_file = tmp_path / "m.py"
    code_file.write_text(code)
    _worker_child.main(["prog", str(code_file), *args])
    line = [x for x in capsys.readouterr().out.splitlines() if x.startswith(SENTINEL)][-1]
    return json.loads(line[len(SENTINEL) :])


def test_a_script_calls_split_and_join_without_importing_it(tmp_path, capsys):
    code = (
        "from build123d import *\n"
        "a, b = split_and_join(Box(300, 100, 10), Plane.YZ, Axis.Z, 0.2)\n"
        "result = Compound(children=[a, b])\n"
    )

    payload = _child(tmp_path, capsys, code)

    assert (payload["ok"], payload["part_count"]) == (True, 2)


@pytest.mark.parametrize("scale", ["nan", "inf", "0", "-1"])
def test_the_child_refuses_a_scale_that_is_no_length(tmp_path, capsys, scale):
    # A negative scale exported a mirrored model as a success, NaN held the
    # kernel inside the export or passed where nothing was exported, and zero
    # and infinity failed in the kernel, as if the model were at fault.
    payload = _child(
        tmp_path, capsys, "from build123d import *\nresult = Box(10, 10, 10)\n", "", scale
    )
    assert payload["ok"] is False
    assert "export scale" in payload["error"]


@pytest.mark.parametrize("volume", [math.nan, math.inf], ids=["nan", "inf"])
def test_the_child_refuses_a_volume_that_is_no_number(tmp_path, capsys, monkeypatch, volume):
    from OCP.GProp import GProp_GProps

    monkeypatch.setattr(GProp_GProps, "Mass", lambda self: volume)
    payload = _child(tmp_path, capsys, "from build123d import *\nresult = Box(10, 10, 10)\n")
    assert payload["ok"] is False
    assert payload["error"].startswith("degenerate solid")


def test_the_child_refuses_a_part_placed_where_no_coordinate_is_a_number(tmp_path, capsys):
    # The part has no volume and the whole keeps its neighbour's, so the volume
    # check passed the build -- with a box no reader downstream could use, and
    # parts the measurement could not finish with inside its budget.
    code = (
        "from build123d import *\n"
        "result = Compound([Pos(float('nan'), 0, 0) * Box(10, 10, 10), Box(10, 10, 10)])\n"
    )
    payload = _child(tmp_path, capsys, code)
    assert payload["ok"] is False
    assert payload["error"].startswith("degenerate solid")


# --- what the child lets a script reach ----------------------------------------
#
# A caller need not have run the gate before it asks for a run, so the child
# holds a script to the policy itself.


def test_the_child_gates_code_its_caller_did_not():
    res = run_code("g = (x for x in [1])\nresult = g.gi_frame\n")

    assert (res.ok, res.repair_context.error_type) == (False, "ValidationError")
    assert res.error.startswith("validation: interpreter-internals attribute access")
    assert res.repair_context.message.startswith("interpreter-internals attribute access")


def test_a_script_that_does_not_parse_fails_on_its_line_as_before():
    res = run_code("result = Box(1, 1,\n")

    assert res.repair_context.error_type == "SyntaxError"
    assert res.repair_context.offending_line == "result = Box(1, 1,"


def test_the_child_hands_a_script_views_not_modules():
    res = run_code("import build123d\nm = build123d.os\nresult = build123d.Box(1, 1, 1)\n")

    assert res.ok is False
    assert "has no attribute 'os'" in res.error


def test_a_result_that_is_no_shape_is_no_build(tmp_path, capsys, monkeypatch):
    # Read by duck typing, an object that only looked like a shape reported
    # whatever it carried: here a volume and a box for a build that made no
    # geometry. Handed to the child directly, past the gate that keeps a script
    # from building one, so this holds the child's own check.
    from types import SimpleNamespace

    from cadless import script_runtime

    def no_solids():
        raise ValueError("no geometry")

    box = SimpleNamespace(size=SimpleNamespace(X=10.0, Y=10.0, Z=10.0))
    made = SimpleNamespace(
        volume=1000.0, bounding_box=lambda: box, solids=no_solids, is_manifold=True
    )
    monkeypatch.setattr(script_runtime, "run_script", lambda code, filename: {"result": made})

    payload = _child(tmp_path, capsys, "result = 1\n")

    assert payload["ok"] is False
    assert "not a shape" in payload["error"]


def test_a_builder_handed_over_as_the_result_is_named_with_what_it_built(tmp_path, capsys):
    code = "from build123d import *\nwith BuildPart() as part:\n    Box(1, 1, 1)\nresult = part\n"

    payload = _child(tmp_path, capsys, code)

    assert (payload["ok"], payload["error_type"]) == (False, "TypeError")
    assert "assign its .part to `result`" in payload["error"]


def test_a_forged_result_line_and_an_exit_never_pass_for_a_build():
    # Held by several layers at once -- the gate, the namespace, print on
    # stderr, the child's own line last -- each pinned by its own test; this one
    # stands for them together and goes red only when they all give way.
    line = f"{SENTINEL} " + json.dumps({"ok": True, "volume": 1.0, "bbox": [1.0, 1.0, 1.0]})

    res = run_code(f"print({line!r})\nraise SystemExit(0)\nresult = 1\n")

    assert res.ok is False


def test_a_kernel_message_never_reaches_the_result_line(tmp_path):
    # The kernel writes its messages to the process's stdout itself, and an
    # unknown font's name comes back in one: run as run_code runs the child.
    code_file = tmp_path / "model.py"
    code_file.write_text(
        "from build123d import *\nresult = extrude(Text('A', 10, font='MARK-no-such-font'), 1)\n"
    )

    proc = subprocess.run(
        [sys.executable, "-m", "cadless._worker_child", str(code_file)],
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert "MARK-no-such-font" in proc.stderr
    assert [line.split(" ", 1)[0] for line in proc.stdout.splitlines()] == [SENTINEL]


@pytest.mark.parametrize("stage", ["build", "post-process"])
def test_the_child_reports_what_ends_a_build_early(tmp_path, capsys, monkeypatch, stage):
    from cadless import _worker_child, script_runtime

    def leave(*args, **kwargs):
        raise SystemExit(0)

    if stage == "build":
        monkeypatch.setattr(script_runtime, "run_script", leave)
    else:
        monkeypatch.setattr(_worker_child, "_summarise", leave)

    payload = _child(tmp_path, capsys, "from build123d import *\nresult = Box(1, 1, 1)\n")

    assert (payload["ok"], payload["error_type"]) == (False, "SystemExit")


# The first side a loop over a set of strings meets follows the string hash seed;
# its length sets the box's size, so the volume shows which side came first.
_SIZED_BY_SET_ORDER = (
    "from build123d import *\n"
    "for side in {'left', 'right', 'front', 'back', 'top', 'bottom'}:\n"
    "    break\n"
    "result = Box(10 + len(side), 10, 10)\n"
)


def test_a_build_does_not_depend_on_the_hash_seed_it_was_started_under(monkeypatch):
    volumes = set()
    for seed in ("1", "2", "3", "4", "5", "random"):
        monkeypatch.setenv("PYTHONHASHSEED", seed)
        res = run_code(_SIZED_BY_SET_ORDER)
        assert res.ok, res.error
        volumes.add(round(res.volume, 6))
    assert len(volumes) == 1, volumes


def test_a_build_keeps_its_asserts_whatever_the_environment_asks(monkeypatch):
    monkeypatch.setenv("PYTHONOPTIMIZE", "1")
    res = run_code("from build123d import *\nassert False, 'kept'\nresult = Box(1, 1, 1)\n")
    assert not res.ok
    assert "AssertionError" in res.error


def test_a_build_does_not_depend_on_the_digit_limit_it_was_started_under(monkeypatch):
    # str() of a 5,001-digit integer raises under the default limit, not under 0.
    code = (
        "from build123d import *\n"
        "try:\n"
        "    n = len(str(10 ** 5000))\n"
        "except ValueError:\n"
        "    n = 1\n"
        "result = Box(10 + n / 1000, 10, 10)\n"
    )
    volumes = set()
    for limit in (None, "0"):
        if limit is None:
            monkeypatch.delenv("PYTHONINTMAXSTRDIGITS", raising=False)
        else:
            monkeypatch.setenv("PYTHONINTMAXSTRDIGITS", limit)
        res = run_code(code)
        assert res.ok, res.error
        volumes.add(round(res.volume, 6))
    assert len(volumes) == 1, volumes


def test_a_build_does_not_fail_on_warnings_the_environment_turns_into_errors(monkeypatch):
    monkeypatch.setenv("PYTHONWARNINGS", "error")
    res = run_code("from build123d import *\nlabel = '\\d'\nresult = Box(1, 1, 1)\n")
    assert res.ok, res.error


def _locale_installed(name: str) -> bool:
    probe = f"import locale; locale.setlocale(locale.LC_ALL, {name!r})"
    return subprocess.run([sys.executable, "-c", probe], capture_output=True).returncode == 0


_needs_latin1 = pytest.mark.skipif(
    not _locale_installed("en_US.ISO8859-1"), reason="needs the en_US.ISO8859-1 locale"
)


@_needs_latin1
def test_a_build_reads_its_script_as_written_whatever_the_locale(monkeypatch):
    # Under a Latin-1 locale the child would read the two UTF-8 bytes of the accented
    # letter as two characters, and the string would have five.
    monkeypatch.setenv("LC_ALL", "en_US.ISO8859-1")
    monkeypatch.setenv("LANG", "en_US.ISO8859-1")
    monkeypatch.delenv("PYTHONUTF8", raising=False)
    res = run_code('from build123d import *\nresult = Box(len("caf\u00e9"), 1, 1)\n')
    assert res.ok, res.error
    assert res.volume == pytest.approx(4.0)


@pytest.mark.skipif(sys.flags.utf8_mode, reason="UTF-8 mode ignores the locale")
@_needs_latin1
def test_a_build_writes_its_script_as_utf8_whatever_this_process_locale():
    import locale

    saved = locale.setlocale(locale.LC_CTYPE)
    locale.setlocale(locale.LC_CTYPE, "en_US.ISO8859-1")
    try:
        res = run_code('from build123d import *\nresult = Box(len("caf\u00e9"), 1, 1)\n')
    finally:
        locale.setlocale(locale.LC_CTYPE, saved)
    assert res.ok, res.error
    assert res.volume == pytest.approx(4.0)


def test_a_script_that_is_not_utf8_text_is_refused_rather_than_raised():
    res = run_code("from build123d import *\nlabel = '\ud800'\nresult = Box(1, 1, 1)\n")
    assert not res.ok
    assert "UTF-8" in res.error


@pytest.mark.parametrize(
    "name",
    [
        "PYTHONPATH",
        "PYTHONHOME",
        "PYTHONUSERBASE",
        "PYTHONNOUSERSITE",
        "PYTHONDONTWRITEBYTECODE",
        "PYTHONPYCACHEPREFIX",
        "PYTHONSAFEPATH",
    ],
)
def test_the_child_keeps_where_python_and_its_packages_live(monkeypatch, name):
    monkeypatch.setenv(name, "/kept")
    assert worker._child_environment()[name] == "/kept"


def test_the_child_gets_no_other_python_setting_and_fixed_ones_in_their_place(monkeypatch):
    monkeypatch.setenv("PYTHONINSPECT", "1")
    monkeypatch.setenv("PYTHONHASHSEED", "7")
    monkeypatch.setenv("PYTHONUTF8", "0")
    env = worker._child_environment()
    assert "PYTHONINSPECT" not in env
    assert (env["PYTHONHASHSEED"], env["PYTHONUTF8"]) == ("0", "1")


# --- what a caller asks of the assembly check ---------------------------------

# A base, two keys in pockets tilted thirty degrees off Z, and a bridge over the
# keys: the search finds no order for it, and the order that works is base, the
# keys, the bridge.
_TILTED_KEYS = (
    "from build123d import *\n"
    "def placed(x, shape):\n"
    "    return Pos(x, 0, 12) * Rot(0, 30, 0) * shape\n"
    "pocket = Pos(0, 0, 12.4) * Box(10.4, 10.4, 55.2)\n"
    "base = Box(60, 40, 30) - placed(-12, pocket) - placed(14, pocket)\n"
    "bridge = (Pos(0, 0, 33) * Box(60, 36, 6) + Pos(0, 15, 22.6) * Box(60, 6, 14.8)\n"
    "          + Pos(0, -15, 22.6) * Box(60, 6, 14.8))\n"
    "keys = [placed(-12, Box(10, 10, 30)), placed(14, Box(10, 10, 30))]\n"
    "result = Compound(children=[base, *keys, bridge])\n"
)
_ALONG_A_KEY = [0.5, 0.0, math.sqrt(0.75)]
_UP = [0.0, 0.0, 1.0]
_KEYS_THEN_BRIDGE = {"order": [0, 1, 2, 3], "releases": [[], _ALONG_A_KEY, _ALONG_A_KEY, _UP]}
_BRIDGE_THEN_KEYS = {"order": [0, 3, 1, 2], "releases": [[], _ALONG_A_KEY, _ALONG_A_KEY, _UP]}


def test_a_budget_the_caller_asks_for_is_kept_where_it_is_the_smaller():
    from cadless import assembly_measure
    from cadless._worker_child import measurement_budget

    share = assembly_measure.MEASUREMENT_TIME_SHARE
    assert measurement_budget(30.0, 0.0, 4.0) == 4.0
    assert measurement_budget(30.0, 0.0, 400.0) == pytest.approx(30.0 * share)
    assert measurement_budget(30.0, 40.0, 4.0) == 0.0


def test_a_worker_with_no_clock_measures_on_the_budget_it_is_asked_for():
    from cadless._worker_child import measurement_budget

    assert measurement_budget(0.0, 5.0, 77.0) == 77.0


def _asked_of_the_measurement(tmp_path, monkeypatch, capsys, request, flag: str) -> dict:
    """What the child hands the measurement of a two-part build asked with ``request``.

    Run with no clock, so the budget handed on is the one asked for however
    long this process has been running.
    """
    from cadless import _worker_child, assembly_measure
    from cadless.assembly_check import AssemblyMeasurements

    seen = {}

    def fake_measure(parts, time_budget, names=None, scale=1.0, **asked):
        seen.update(asked, time_budget=time_budget)
        return AssemblyMeasurements(part_bboxes=[], gaps=[], order=None)

    monkeypatch.setattr(assembly_measure, "measure_assembly", fake_measure)
    code_file, check_file = tmp_path / "m.py", tmp_path / "check.json"
    code_file.write_text(
        "from build123d import *\nresult = Compound([Box(1, 1, 1), Pos(5, 0, 0) * Box(1, 1, 1)])\n"
    )
    check_file.write_text(json.dumps(request))
    _worker_child.main(["prog", str(code_file), "", "1", flag, "", str(check_file)])
    capsys.readouterr()
    return seen


def test_the_child_hands_what_was_asked_to_the_measurement(tmp_path, monkeypatch, capsys):
    request = {
        "order": [1, 0],
        "releases": [[], [0, 0, 2]],
        "binds": False,
        "time_budget": 0.5,
        "probe_budget": 9,
    }

    # the measurement flag is left off: a request asks for the measurement itself
    seen = _asked_of_the_measurement(tmp_path, monkeypatch, capsys, request, "")

    assert seen == {
        "order": [1, 0],
        "releases": [[], [0.0, 0.0, 1.0]],
        "binds": False,
        "probe_budget": 9,
        "time_budget": 0.5,
    }


def test_a_request_for_budgets_alone_declares_no_order_to_the_measurement(
    tmp_path, monkeypatch, capsys
):
    seen = _asked_of_the_measurement(tmp_path, monkeypatch, capsys, {"time_budget": 0.25}, "1")

    assert seen == {"time_budget": 0.25}


@pytest.mark.parametrize(
    "written",
    ['{"order": [0, 0]}', "{", "[" * 100_000],
    ids=["a part twice", "no JSON", "nesting past the parser"],
)
def test_the_child_refuses_a_request_it_cannot_use_before_the_script_runs(
    tmp_path, capsys, written
):
    # The script fails on its own first line, so an error that is the request's
    # is one that came before it ran.
    check_file = tmp_path / "check.json"
    check_file.write_text(written)

    payload = _child(
        tmp_path, capsys, "raise SystemExit('ran')\n", "", "1", "1", "30", str(check_file)
    )

    assert payload["ok"] is False
    assert payload["error"].startswith("the assembly check's request cannot be used: ")


def test_the_child_says_why_it_could_not_use_a_request(tmp_path, capsys):
    check_file = tmp_path / "check.json"
    check_file.write_text('{"order": [0, 0]}')

    payload = _child(tmp_path, capsys, "result = 1\n", "", "1", "1", "30", str(check_file))

    assert payload["error"].endswith("the order does not name each of the 2 parts once")


def test_a_one_part_build_is_measured_when_an_order_is_declared_for_it(tmp_path, capsys):
    # Never measured otherwise; with an order declared it has to be said that
    # the order is not this build's.
    check_file = tmp_path / "check.json"
    check_file.write_text(json.dumps({"order": [0, 1]}))
    code = "from build123d import *\nresult = Box(1, 1, 1)\n"

    payload = _child(tmp_path, capsys, code, "", "1", "1", "30", str(check_file))

    assert payload["ok"] is True
    assert payload["assembly"]["declared"] == {"binds": True, "outcome": "mismatch"}
    assert payload["assembly"]["order"] is None
    assert "assembly" not in _child(tmp_path, capsys, code, "", "1", "1", "30")


def test_run_code_confirms_an_order_the_search_does_not_find():
    from cadless.assembly_check import CheckRequest

    searched = run_code(_TILTED_KEYS, check_assembly=True)
    assert searched.ok, searched.error
    assert searched.assembly.order is None
    assert searched.assembly.declared is None

    # no measurement flag: the request asks for it
    res = run_code(_TILTED_KEYS, check=CheckRequest(**_KEYS_THEN_BRIDGE))

    assert res.ok, res.error
    assert res.assembly.declared == {"binds": True, "outcome": "confirmed"}
    assert res.assembly.order == [0, 1, 2, 3]
    assert res.assembly.releases[1] == pytest.approx(_ALONG_A_KEY)


def test_run_code_says_where_a_declared_order_stops():
    from cadless.assembly_check import CheckRequest

    res = run_code(_TILTED_KEYS, check=CheckRequest(**_BRIDGE_THEN_KEYS))

    assert res.ok, res.error
    assert res.assembly.order is None
    assert res.assembly.trapped == []
    assert res.assembly.declared == {
        "binds": True,
        "outcome": "blocked",
        "part": 1,
        "heading": pytest.approx(_ALONG_A_KEY),
        "blockers": [3],
    }


def _spawning(monkeypatch) -> list:
    """Have a child's run recorded rather than made: each command, with what its request file held."""
    runs = []

    def run(cmd, **kwargs):
        # Its place is there and empty where kinds follow a run that made no request.
        asked = json.loads(Path(cmd[8]).read_text()) if len(cmd) > 8 and cmd[8] else None
        runs.append((cmd, asked))
        line = f"{SENTINEL} " + json.dumps({"ok": True, "volume": 1.0, "bbox": [1.0, 1.0, 1.0]})
        return subprocess.CompletedProcess(cmd, 0, stdout=line + "\n", stderr="")

    stand_in = types.SimpleNamespace(run=run, TimeoutExpired=subprocess.TimeoutExpired)
    monkeypatch.setattr(worker, "subprocess", stand_in)
    return runs


def test_a_request_reaches_the_child_as_a_file_named_last(monkeypatch):
    from cadless.assembly_check import CheckRequest

    runs = _spawning(monkeypatch)

    run_code("result = 1\n", check=CheckRequest(order=[1, 0], releases=[[], [0, 0, 2]]))

    (cmd, asked) = runs[0]
    assert len(cmd) == 9
    assert cmd[6] == "1"  # the request asks for the measurement
    assert asked == {
        "order": [1, 0],
        "releases": [[], [0.0, 0.0, 1.0]],
        "binds": True,
        "time_budget": None,
        "probe_budget": None,
    }


def test_with_no_request_the_child_is_run_as_it_was(monkeypatch):
    runs = _spawning(monkeypatch)

    run_code("result = 1\n", check_assembly=True)

    (cmd, asked) = runs[0]
    assert cmd[1:3] == ["-m", "cadless._worker_child"]
    assert len(cmd) == 8
    assert asked is None


@pytest.mark.parametrize(
    "check",
    # the last is a whole number above nought, and one no file or body could carry
    [{"order": [0, 0]}, [0, 1], "order", {"probe_budget": 10**5000}],
    ids=["a part twice", "a list", "text", "a budget too large to send"],
)
def test_a_request_that_cannot_be_used_fails_the_run_with_nothing_spawned(monkeypatch, check):
    runs = _spawning(monkeypatch)

    res = run_code("result = 1\n", check=check)

    assert res.ok is False
    assert "request" in res.error
    assert runs == []


def test_a_request_object_holding_what_is_no_list_is_refused_as_any_other_is(monkeypatch):
    # Copied before it was read, such a request raised out of the call instead.
    from cadless.assembly_check import CheckRequest

    runs = _spawning(monkeypatch)

    res = run_code("result = 1\n", check=CheckRequest(order=(part for part in (0, 1))))

    assert res.ok is False
    assert "request" in res.error
    assert runs == []


_NO_RECORD = {
    "ok": True,
    "volume": 1.0,
    "bbox": [1.0, 1.0, 1.0],
    "assembly": {
        "part_bboxes": [[1.0, 1.0, 1.0], [1.0, 1.0, 1.0]],
        "gaps": [[0, 1, 0.2]],
        "order": [1, 0],
        "releases": [[1.0, 0.0, 0.0], []],
    },
}


def test_an_order_that_binds_is_not_established_by_a_result_that_says_nothing_of_it(monkeypatch):
    # A worker of an earlier release takes no request: it searches, and what it
    # finds is an order nobody declared.
    from cadless.assembly_check import CheckRequest, evaluate_assembly
    from cadless.printer_profile import AssemblySpec, BuildVolume

    _forged(monkeypatch, _NO_RECORD)

    res = run_code("result = 1\n", check=CheckRequest(order=[0, 1]))

    assert res.ok
    assert res.assembly.order is None
    assert res.assembly.releases == []
    assert res.assembly.unchecked == [
        "assembly order: the declared order was not checked: the worker that ran the build "
        "takes no request"
    ]
    spec = AssemblySpec(volume=BuildVolume(210.0, 200.0, 195.0), clearance_mm=0.2)
    assert not evaluate_assembly(res.assembly, spec).ok


def test_a_search_that_trapped_parts_is_not_the_answer_to_an_order_that_binds(monkeypatch):
    from cadless.assembly_check import CheckRequest

    trapped = {**_NO_RECORD["assembly"], "order": None, "trapped": [0, 1], "releases": []}
    _forged(monkeypatch, {**_NO_RECORD, "assembly": trapped})

    res = run_code("result = 1\n", check=CheckRequest(order=[0, 1]))

    assert res.assembly.trapped == []
    assert len(res.assembly.unchecked) == 1


def test_an_order_that_binds_is_not_established_by_a_result_with_no_measurement(monkeypatch):
    from cadless.assembly_check import CheckRequest

    _forged(monkeypatch, {"ok": True, "volume": 1.0, "bbox": [1.0, 1.0, 1.0]})

    res = run_code("result = 1\n", check=CheckRequest(order=[0, 1]))

    assert res.assembly is not None
    assert any("was not checked" in reason for reason in res.assembly.unchecked)


def test_a_record_that_says_the_order_did_not_bind_does_not_answer_for_one_that_does(monkeypatch):
    from cadless.assembly_check import CheckRequest

    hinted = {**_NO_RECORD["assembly"], "declared": {"binds": False, "outcome": "confirmed"}}
    _forged(monkeypatch, {**_NO_RECORD, "assembly": hinted})

    res = run_code("result = 1\n", check=CheckRequest(order=[1, 0]))

    assert res.assembly.order is None
    assert any("was not checked" in reason for reason in res.assembly.unchecked)


@pytest.mark.parametrize(
    "check",
    [{"order": [0, 1], "binds": False}, {"time_budget": 5.0}],
    ids=["a hint", "budgets alone"],
)
def test_a_result_that_says_nothing_of_an_order_stands_where_none_was_bound(monkeypatch, check):
    _forged(monkeypatch, _NO_RECORD)

    res = run_code("result = 1\n", check=check)

    assert res.assembly.order == [1, 0]
    assert res.assembly.unchecked == []


def test_a_result_that_answers_for_the_order_is_left_as_it_came(monkeypatch):
    from cadless.assembly_check import CheckRequest

    confirmed = {**_NO_RECORD["assembly"], "declared": {"binds": True, "outcome": "confirmed"}}
    _forged(monkeypatch, {**_NO_RECORD, "assembly": confirmed})

    res = run_code("result = 1\n", check=CheckRequest(order=[1, 0]))

    assert res.assembly.order == [1, 0]
    assert res.assembly.unchecked == []


# --- the kinds a caller asks a run for --------------------------------------

_ONE_BOX = "from build123d import *\nresult = Box(8, 8, 8)\n"
_REFUSED_KINDS = "the run's export kinds cannot be used: "


def _written(tmp_path, capsys, code: str, kinds: str | None) -> tuple[dict, list[str]]:
    """The child's result for ``code`` asked for ``kinds``, and the files then in its folder.

    A folder of its own: the child's script is written beside it, not into it.
    """
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    asked = () if kinds is None else ("", kinds)
    payload = _child(tmp_path, capsys, code, str(out), "1", "", "30", *asked)
    return payload, sorted(path.name for path in out.iterdir())


def test_a_build_of_several_parts_writes_the_kinds_asked_and_their_whole_files(tmp_path, capsys):
    payload, files = _written(tmp_path, capsys, _CAR, '["glb", "step"]')

    assert payload["ok"] is True
    assert files == sorted(
        [f"model_p{part}.{kind}" for part in range(4) for kind in ("glb", "step")]
        + ["assembly.glb", "assembly.step"]
    )
    assert payload["glb_path"].endswith("model_p0.glb")
    assert "stl_path" not in payload
    assert "obj_path" not in payload


def test_no_whole_file_is_written_where_neither_of_its_kinds_is_asked(tmp_path, capsys):
    payload, files = _written(tmp_path, capsys, _CAR, '["stl"]')

    assert files == [f"model_p{part}.stl" for part in range(4)]
    assert "assembly_glb_path" not in payload
    assert "assembly_step_path" not in payload


def test_a_kind_not_asked_for_leaves_nothing_of_an_earlier_build_behind(tmp_path, capsys):
    _written(tmp_path, capsys, _CAR, None)
    motion = tmp_path / "out" / "assembly.json"
    motion.write_text("{}")

    payload, files = _written(tmp_path, capsys, _CAR, '["glb"]')

    assert files == sorted(
        [f"model_p{part}.glb" for part in range(4)] + ["assembly.glb", "assembly.json"]
    )
    assert payload["assembly_glb_path"].endswith("assembly.glb")
    assert "assembly_step_path" not in payload
    assert motion.read_text() == "{}"


def test_a_one_part_build_asked_for_one_kind_clears_a_build_of_several_parts(tmp_path, capsys):
    _written(tmp_path, capsys, _CAR, None)

    payload, files = _written(tmp_path, capsys, _ONE_BOX, '["step"]')

    assert payload["ok"] is True
    assert files == ["model.step"]


def test_the_child_walks_the_registry_as_it_stands_for_what_it_writes_and_clears(
    tmp_path, capsys, monkeypatch
):
    from cadless import exporters

    def writes(kind):
        def export(shape, out_dir, name="model"):
            path = Path(out_dir) / f"{name}.{kind}"
            path.write_text(kind)
            return str(path)

        return export

    monkeypatch.setattr(exporters, "EXPORTERS", {"mesh": writes("mesh"), "brep": writes("brep")})
    out = tmp_path / "out"
    out.mkdir()
    (out / "model.brep").write_text("an earlier build's")

    payload, files = _written(tmp_path, capsys, _ONE_BOX, '["mesh"]')

    assert payload["ok"] is True
    assert files == ["model.mesh"]
    assert payload["mesh_path"].endswith("model.mesh")
    assert "brep_path" not in payload


@pytest.mark.parametrize(
    ("argument", "fault"),
    [
        ("[]", "the list names no kind"),
        ('["dxf"]', "dxf is not an export kind (step, glb, stl, obj)"),
        ('"stl"', "the kinds are the text 'stl' rather than a list of names"),
        ('["glb", ["stl"]]', "entry 1 is not text"),
    ],
    ids=["an empty list", "an unknown name", "a bare name", "an entry that is a list"],
)
def test_the_child_refuses_kinds_it_cannot_use_before_the_script_runs(
    tmp_path, capsys, argument, fault
):
    # The script fails on its own first line, so an error that is the request's
    # is one that came before it ran.
    payload, _ = _written(tmp_path, capsys, "raise SystemExit('ran')\n", argument)

    assert payload["ok"] is False
    assert payload["error"] == _REFUSED_KINDS + fault


@pytest.mark.parametrize(
    "argument", ["{", "[" * 100_000], ids=["no JSON", "nesting past the parser"]
)
def test_the_child_refuses_kinds_it_cannot_read_before_the_script_runs(tmp_path, capsys, argument):
    payload, _ = _written(tmp_path, capsys, "raise SystemExit('ran')\n", argument)

    assert payload["ok"] is False
    assert payload["error"].startswith(_REFUSED_KINDS)


def test_kinds_reach_the_child_as_text_after_an_empty_place_for_the_request_file(monkeypatch):
    runs = _spawning(monkeypatch)

    run_code("result = 1\n", kinds=["glb", "step", "glb"])

    (cmd, _) = runs[0]
    assert cmd[8:] == ["", '["step", "glb"]']
    assert cmd[6] == ""  # kinds ask for no measurement


def test_kinds_follow_the_request_file_where_both_are_asked(monkeypatch):
    from cadless.assembly_check import CheckRequest

    runs = _spawning(monkeypatch)

    run_code("result = 1\n", check=CheckRequest(order=[1, 0]), kinds=("obj",))

    (cmd, asked) = runs[0]
    assert len(cmd) == 10
    assert asked["order"] == [1, 0]
    assert cmd[9] == '["obj"]'


@pytest.mark.parametrize(
    ("kinds", "fault"),
    [
        ([], "the list names no kind"),
        ("stl", "the kinds are the text 'stl' rather than a list of names"),
        (["glb", "dxf"], "dxf is not an export kind (step, glb, stl, obj)"),
        (["assembly_glb"], "assembly_glb is a whole-assembly file, written with its kind glb"),
    ],
    ids=["an empty list", "a bare name", "an unknown name", "a whole file"],
)
def test_kinds_that_cannot_be_used_fail_the_run_with_nothing_spawned(monkeypatch, kinds, fault):
    runs = _spawning(monkeypatch)

    res = run_code("result = 1\n", kinds=kinds)

    assert (res.ok, res.error) == (False, _REFUSED_KINDS + fault)
    assert runs == []


_TWO_BLOCKS = (
    "from build123d import *\n"
    "result = Compound([Box(10, 10, 10), Pos(30, 0, 0) * Box(10, 10, 10)])\n"
)


def test_run_code_writes_the_kind_asked_for_a_build_whose_order_it_confirms(tmp_path):
    from cadless.assembly_check import CheckRequest

    res = run_code(
        _TWO_BLOCKS,
        export_dir=str(tmp_path),
        check=CheckRequest(order=[1, 0], releases=[[], [-3, 0, 0]]),
        kinds=["glb"],
    )

    assert res.ok, res.error
    assert res.assembly.declared == {"binds": True, "outcome": "confirmed"}
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "assembly.glb",
        "model_p0.glb",
        "model_p1.glb",
    ]
    assert res.glb_path == str(tmp_path / "model_p0.glb")
    assert res.assembly_glb_path == str(tmp_path / "assembly.glb")
    assert (res.step_path, res.stl_path, res.obj_path, res.assembly_step_path) == (None,) * 4


def _less_its_time(path: Path) -> bytes:
    """A STEP file's bytes less the header line that carries when it was written."""
    lines = path.read_bytes().split(b"\n")
    return b"\n".join(line for line in lines if not line.startswith(b"FILE_NAME("))


def test_a_kind_asked_for_is_written_as_a_run_asked_for_nothing_writes_it(tmp_path):
    every, some = tmp_path / "every", tmp_path / "some"

    assert run_code(_CAR, export_dir=str(every)).ok
    assert run_code(_CAR, export_dir=str(some), kinds=["glb", "step"]).ok

    for name in ("model_p0.glb", "model_p3.glb", "assembly.glb"):
        assert (some / name).read_bytes() == (every / name).read_bytes(), name
    for name in ("model_p0.step", "assembly.step"):
        assert _less_its_time(some / name) == _less_its_time(every / name), name


def test_a_kind_the_api_knows_and_its_child_does_not_fails_the_run_once(tmp_path, monkeypatch):
    # The child is another process, with a registry of its own.
    from cadless import exporters

    monkeypatch.setattr(exporters, "EXPORTERS", {**exporters.EXPORTERS, "brep": None})
    (tmp_path / "model.stl").write_text("an earlier build's")

    res = run_code(_ONE_BOX, export_dir=str(tmp_path), kinds=["brep"])

    assert res.ok is False
    assert res.error == _REFUSED_KINDS + "brep is not an export kind (step, glb, stl, obj)"
    assert (tmp_path / "model.stl").read_text() == "an earlier build's"
