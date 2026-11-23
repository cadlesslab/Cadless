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
