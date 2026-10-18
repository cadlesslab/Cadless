"""The assembly motion's numbers, as the engine writes them and the viewport reads them.

The engine writes the motion and the frontend validates it, in two languages that
share no code. Some of the frontend's limits only mean something next to the
engine's own values: a heading threshold above the engine's refuses a heading
the engine measured; a cap on the explode fraction below the fraction the engine
writes refuses every motion it produces; and a cap on one part's travel below
the furthest the engine measures refuses the assemblies that need it most. A
refused motion hides the player with nothing failing. These read both sides and
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


def test_the_viewport_accepts_the_furthest_travel_the_engine_writes():
    # The worst case for the measured travel: parts that all sit in one another
    # and leave along the same diagonal, so each has to clear every part before
    # it at the far end of its own way out. The viewport caps one part's travel
    # at the part count times a diagonal and a spacing; a cap below what this
    # writes would hide the player for a payload the engine produced.
    import numpy as np

    count = 5
    corners = np.array([[x, y, z] for x in (0, 10) for y in (0, 10) for z in (0, 10)], float)
    cube = np.stack([corners[[0, 1, 3]], corners[[4, 6, 7]], corners[[0, 4, 5]]])
    heading = [1.0, 1.0, 1.0]
    payload = assembly_guide.motion_payload(
        list(range(count)), [[]] + [heading] * (count - 1), [cube] * count
    )
    assert max(payload["travel"]) <= count * _ts_number("MAX_TRAVEL_PER_PART")


def test_the_travel_cap_covers_a_diagonal_and_a_spacing_per_part():
    # The bound the cap stands for: along any heading the model spans at most its
    # diagonal, and each part placed before adds that and a spacing at most.
    assert _ts_number("MAX_TRAVEL_PER_PART") >= 3**0.5 + assembly_guide.EXPLODE_FRACTION
