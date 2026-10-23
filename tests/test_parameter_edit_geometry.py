"""A parameter edit rebuilt for real: two boxes whose gap is a parameter.

The edit the chat's ``set_parameters`` makes, through ``_default_reparametrize``,
with build123d doing the geometry and the worker measuring it -- what the scripted
tests of the agent loop and of ``rebuild`` leave out.
"""

import pytest

from cadless.agent import _default_reparametrize
from cadless.printer_profile import AssemblySpec, BuildVolume

pytestmark = pytest.mark.build123d

TWO_BOXES = (
    "from build123d import *\n"
    'params = {"gap": 5}\n'
    "left = Box(10, 10, 10)\n"
    'left.label = "left"\n'
    'right = Pos(10 + params["gap"], 0, 0) * Box(10, 10, 10)\n'
    'right.label = "right"\n'
    "result = Compound(children=[left, right])\n"
)
SPEC = AssemblySpec(volume=BuildVolume(210.0, 200.0, 195.0), clearance_mm=0.2)


def test_an_edit_that_makes_two_parts_overlap_is_caught(tmp_path):
    out = _default_reparametrize(TWO_BOXES, {"gap": -5}, export_dir=str(tmp_path))

    assert out["ok"] is True  # nobody asked for an assembly, so it is kept...
    assert out["assembly"]["ok"] is False  # ...with the overlap on it
    assert any("left and right share" in line for line in out["assembly"]["failures"])


def test_an_edit_that_breaks_an_assembly_is_refused(tmp_path):
    out = _default_reparametrize(TWO_BOXES, {"gap": -5}, export_dir=str(tmp_path), assembly=SPEC)

    assert out["ok"] is False
    assert out["error"].startswith("assembly: ")
    assert out["assembly"]["ok"] is False


def test_an_accepted_edit_is_described(tmp_path):
    out = _default_reparametrize(TWO_BOXES, {"gap": 10}, export_dir=str(tmp_path))

    assert out["ok"] is True, out["error"]
    assert out["guide"]["parts"] == ["left", "right"]


def test_an_edit_exports_its_files_where_the_turn_collects_them(tmp_path):
    out = _default_reparametrize(TWO_BOXES, {"gap": 10}, export_dir=str(tmp_path))

    assert out["ok"] is True, out["error"]
    assert out["glb_path"] is not None
    assert str(tmp_path) in out["glb_path"]
