from build123d import *

params = {
    "cover_length": 90,
    "cover_width": 30,
    "cover_height": 25,
    "wall": 2,
}

# Cover blank centered in X/Y, resting on Z=0.
part = Box(
    params["cover_length"],
    params["cover_width"],
    params["cover_height"],
    align=(Align.CENTER, Align.CENTER, Align.MIN),
)

# Hollow from below, leaving walls and a roof.
cavity = Box(
    params["cover_length"] - 2 * params["wall"],
    params["cover_width"] - 2 * params["wall"],
    params["cover_height"] - params["wall"] + 1,
    align=(Align.CENTER, Align.CENTER, Align.MIN),
)
part -= Pos(0, 0, -1) * cavity

result = part
