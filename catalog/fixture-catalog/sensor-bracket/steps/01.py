from build123d import *

params = {
    "base_length": 40,
    "base_width": 30,
    "base_thickness": 4,
}

# Base plate from X=0 to X=40, centered in Y, resting on Z=0.
part = Box(
    params["base_length"],
    params["base_width"],
    params["base_thickness"],
    align=(Align.MIN, Align.CENTER, Align.MIN),
)

result = part
