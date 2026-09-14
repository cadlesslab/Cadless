"""A guide drawn from a build that really happened.

Everything here is real but the model: the worker executes build123d, exports one
STL per solid, measures the parts, and the pipeline checks them and draws the
guide from what it measured. Only the generator is scripted, because what a model
would contribute is the wording and this is about the rest.

It is the one place the drawing half runs end to end. The unit tests around it
hand meshes to a function; here the meshes are the files a build actually wrote,
and a guide that cannot find them produces no frames rather than failing loudly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cadless.assembly_guide import STEP_FRAME_MIN_PARTS
from cadless.config import Settings
from cadless.pipeline import Pipeline
from cadless.printer_profile import AssemblySpec, BuildVolume

pytestmark = pytest.mark.build123d

SPEC = AssemblySpec(volume=BuildVolume(210.0, 200.0, 195.0), clearance_mm=0.2)


def _stack(count: int) -> str:
    """A tower of boxes, each sitting one clearance above the last.

    Not interlocked: what this exercises is the path from a real build to a real
    picture, and a dovetail would only make the geometry slower to execute.
    """
    boxes = " + ".join(f"Pos(0, 0, {i * 10.2}) * Box(20, 20, 10)" for i in range(count))
    return f"from build123d import *\nresult = {boxes}\n"


class ScriptedGen:
    def __init__(self, code: str):
        self._code = code

    def generate(self, intent, grounding=None, **kw):
        return self._code

    def refine(self, intent, prior_code, **kw):
        return self._code

    def repair(self, intent, code, error, context=None, **kw):
        return self._code


def _build(count: int, export_dir: Path):
    pipeline = Pipeline(generator=ScriptedGen(_stack(count)), config=Settings())
    return pipeline.run("a tall bracket", export_dir=str(export_dir), assembly=SPEC)


def test_a_real_two_part_build_comes_back_with_a_guide_and_one_exploded_drawing(tmp_path):
    result = _build(2, tmp_path)

    assert result.ok, result.error
    assert result.assembly is not None and result.assembly["ok"] is True
    assert result.guide is not None
    # The order is the one the search established, and the steps follow it.
    assert result.guide["steps"][0].startswith("Start with ")
    assert len(result.guide["steps"]) == 2
    assert result.guide["frames"] == 1

    drawn = sorted(tmp_path.glob("guide_f*.png"))
    assert [p.name for p in drawn] == ["guide_f0.png"]
    assert drawn[0].read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_a_real_four_part_build_is_drawn_a_step_at_a_time(tmp_path):
    assert STEP_FRAME_MIN_PARTS == 4, "this fixture is sized to sit on the threshold"
    result = _build(4, tmp_path)

    assert result.ok, result.error
    assert result.guide is not None
    assert len(result.guide["steps"]) == 4
    assert result.guide["frames"] == 4

    drawn = sorted(tmp_path.glob("guide_f*.png"))
    assert [p.name for p in drawn] == [f"guide_f{i}.png" for i in range(4)]
    for path in drawn:
        assert path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_the_steps_name_every_part_and_the_joint_each_one_is_made_at(tmp_path):
    result = _build(3, tmp_path)

    assert result.ok, result.error
    assert result.guide is not None
    parts = result.guide["parts"]
    assert len(parts) == 3
    # Every part is named somewhere in the steps, and every step after the first
    # says what the part it adds is joined to.
    text = " ".join(result.guide["steps"])
    for name in parts:
        assert name in text
    for step in result.guide["steps"][1:]:
        assert " to " in step


def test_a_single_part_build_writes_no_guide_and_no_drawings(tmp_path):
    pipeline = Pipeline(
        generator=ScriptedGen("from build123d import *\nresult = Box(20, 20, 10)\n"),
        config=Settings(),
    )
    result = pipeline.run("a plate", export_dir=str(tmp_path), assembly=SPEC)

    assert result.ok, result.error
    assert result.guide is None
    assert list(tmp_path.glob("guide_f*.png")) == []


def test_a_rebuild_into_the_same_directory_leaves_no_frame_from_the_last_one(tmp_path):
    # The frames are registered from whatever is in the directory when the build
    # finishes, so a leftover would be filed against this version as if it
    # belonged to it.
    _build(4, tmp_path)
    assert len(list(tmp_path.glob("guide_f*.png"))) == 4

    result = _build(2, tmp_path)
    assert result.ok, result.error
    assert [p.name for p in sorted(tmp_path.glob("guide_f*.png"))] == ["guide_f0.png"]
