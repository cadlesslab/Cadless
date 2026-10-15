"""The assembly motion's numbers, as the engine writes them and the viewport reads them.

The engine writes the motion and the frontend validates it, in two languages that
share no code. Two of the frontend's limits only mean something next to the
engine's own values: a heading threshold that differs lets the viewport move a
part the engine left standing, or refuse one it measured; and a cap on the
explode fraction below the fraction the engine writes refuses every motion it
produces, which hides the player with nothing failing. These read both sides and
hold them together.
"""

from __future__ import annotations

import re
from pathlib import Path

from cadless import assembly_guide

_MOTION_TS = (
    Path(__file__).resolve().parents[1] / "frontend" / "src" / "viewport" / "assemblyMotion.ts"
)


def _ts_number(name: str) -> float:
    found = re.search(rf"^const {name} = ([0-9.eE+-]+);", _MOTION_TS.read_text(), re.MULTILINE)
    assert found, f"{name} is no longer a numeric constant in {_MOTION_TS.name}"
    return float(found.group(1))


def test_the_viewport_and_the_engine_agree_on_what_is_a_heading():
    assert _ts_number("HEADING_EPSILON") == assembly_guide._HEADING_EPSILON


def test_the_viewport_accepts_the_fraction_the_engine_writes():
    assert 0 < assembly_guide.EXPLODE_FRACTION <= _ts_number("MAX_EXPLODE_FRACTION")
