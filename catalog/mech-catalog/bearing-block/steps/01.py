from build123d import *

params = {
    "base_length": 100,
    "base_width": 40,
    "base_thickness": 12,
}

# Base plate: 100 x 40 mm, 12 mm thick. Centered in X/Y, resting on Z=0.
result = Box(
    params["base_length"],
    params["base_width"],
    params["base_thickness"],
    align=(Align.CENTER, Align.CENTER, Align.MIN),
)
