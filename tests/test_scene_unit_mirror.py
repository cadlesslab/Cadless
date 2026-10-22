"""The scene's unit, as the engine writes it and the viewport reads it.

The engine writes a GLB from a build in millimetres, and the viewport labels a
length it measures in the scene in millimetres. The two share no code: a viewport
reading the scene in another unit than the one the file is written in labels every
measurement a thousand times off, with nothing failing. This reads both sides and
holds them together.
"""

from __future__ import annotations

import pytest

from tests.ts_constants import FRONTEND_SRC, ts_number

pytestmark = pytest.mark.build123d

_INSPECTION_TS = FRONTEND_SRC / "viewport" / "inspection.ts"


def test_the_viewport_reads_a_glb_in_the_unit_the_engine_writes_it(tmp_path):
    from build123d import Box

    from cadless.exporters import export_glb
    from tests.glb_bounds import glb_scene_bounds

    low, high = glb_scene_bounds(export_glb(Box(80, 40, 6), str(tmp_path)))
    longest = max(high[axis] - low[axis] for axis in range(3))

    assert longest * ts_number(_INSPECTION_TS, "MM_PER_SCENE_UNIT") == pytest.approx(80, abs=0.01)
