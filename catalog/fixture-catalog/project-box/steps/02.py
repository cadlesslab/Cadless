from build123d import *

params = {
    "outer_length": 80,
    "outer_width": 50,
    "outer_height": 22,
    "wall": 3,
    "floor": 2.5,
}

# Enclosure body blank: a solid block, base on Z=0.
part = Box(
    params["outer_length"],
    params["outer_width"],
    params["outer_height"],
    align=(Align.CENTER, Align.CENTER, Align.MIN),
)

# Hollow the interior, leaving walls and a floor; open at the top.
cavity = Box(
    params["outer_length"] - 2 * params["wall"],
    params["outer_width"] - 2 * params["wall"],
    params["outer_height"],
    align=(Align.CENTER, Align.CENTER, Align.MIN),
)
part -= Pos(0, 0, params["floor"]) * cavity

result = part
