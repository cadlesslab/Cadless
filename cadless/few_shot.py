"""Curated natural-language -> build123d few-shot examples.

Each example is a (prompt, code) pair where the code:
  * imports only allow-listed modules,
  * assigns the final solid to ``result``,
  * compiles and executes under build123d (verified in tests/test_few_shot.py).

These ground the model in the exact API subset and conventions it must use.
build123d is used in **algebra mode** (objects compose with +, -, & and are
placed with ``Pos(...) * obj``), which is the most LLM-robust style.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Example:
    prompt: str
    code: str


FEW_SHOT: list[Example] = [
    Example(
        prompt="A rectangular plate 40 mm long, 20 mm wide and 10 mm thick.",
        code="""\
from build123d import *

params = {"length": 40, "width": 20, "thickness": 10}
result = Box(params["length"], params["width"], params["thickness"])
""",
    ),
    Example(
        prompt="A cylinder 30 mm in diameter and 50 mm tall.",
        code="""\
from build123d import *

params = {"diameter": 30, "height": 50}
result = Cylinder(radius=params["diameter"] / 2, height=params["height"])
""",
    ),
    Example(
        prompt="A 60x40x8 mm plate with a 10 mm diameter hole through the centre.",
        code="""\
from build123d import *

params = {"length": 60, "width": 40, "thickness": 8, "hole_dia": 10}
plate = Box(params["length"], params["width"], params["thickness"])
hole = Cylinder(radius=params["hole_dia"] / 2, height=params["thickness"])
result = plate - hole
""",
    ),
    Example(
        prompt="A 20 mm cube with all edges rounded by a 2 mm fillet.",
        code="""\
from build123d import *

params = {"size": 20, "fillet_radius": 2}
cube = Box(params["size"], params["size"], params["size"])
result = fillet(cube.edges(), radius=params["fillet_radius"])
""",
    ),
    Example(
        prompt="An L-bracket: a 50x30 mm base 5 mm thick with a 45 mm tall wall 5 mm "
        "thick rising from one short edge.",
        code="""\
from build123d import *

params = {"base_length": 50, "base_width": 30, "thickness": 5, "wall_height": 45}
base = Box(params["base_length"], params["base_width"], params["thickness"])
wall = Pos(
    -(params["base_length"] - params["thickness"]) / 2,
    0,
    (params["wall_height"] - params["thickness"]) / 2 + params["thickness"] / 2,
) * Box(params["thickness"], params["base_width"], params["wall_height"])
result = base + wall
""",
    ),
    Example(
        prompt="A washer with 30 mm outer diameter, 12 mm inner diameter and 4 mm thickness.",
        code="""\
from build123d import *

params = {"outer_dia": 30, "inner_dia": 12, "thickness": 4}
outer = Cylinder(radius=params["outer_dia"] / 2, height=params["thickness"])
inner = Cylinder(radius=params["inner_dia"] / 2, height=params["thickness"])
result = outer - inner
""",
    ),
    Example(
        prompt="A hexagonal prism 25 mm across the flats and 15 mm tall.",
        code="""\
from build123d import *

params = {"across_flats": 25, "height": 15}
profile = RegularPolygon(
    radius=params["across_flats"] / 2, side_count=6, major_radius=False
)
result = extrude(profile, amount=params["height"])
""",
    ),
]


def render_few_shot() -> str:
    """Render the examples as prompt text (a series of request/response pairs)."""
    blocks = []
    for ex in FEW_SHOT:
        blocks.append(f"Request: {ex.prompt}\nResponse:\n```python\n{ex.code}```")
    return "\n\n".join(blocks)
