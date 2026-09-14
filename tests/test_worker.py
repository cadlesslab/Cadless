"""Execution-worker tests. Marked build123d: spawns subprocesses that
import the OCCT kernel."""

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
