from build123d import *

params = {
    "base_length": 40,
    "base_width": 30,
    "base_thickness": 4,
    "upright_thickness": 4,
    "upright_height": 50,
}

# Base plate from X=0 to X=40, centered in Y, resting on Z=0.
part = Box(
    params["base_length"],
    params["base_width"],
    params["base_thickness"],
    align=(Align.MIN, Align.CENTER, Align.MIN),
)

# Upright sensor face sharing the corner root with the base.
upright = Box(
    params["upright_thickness"],
    params["base_width"],
    params["upright_height"],
    align=(Align.MIN, Align.CENTER, Align.MIN),
)
part += upright

result = part
