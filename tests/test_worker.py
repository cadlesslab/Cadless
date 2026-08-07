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
