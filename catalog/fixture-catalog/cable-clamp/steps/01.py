from build123d import *

params = {
    "base_length": 40,
    "base_width": 16,
    "base_thickness": 5,
}

# Clamp base bar centered in X/Y, resting on Z=0.
part = Box(
    params["base_length"],
    params["base_width"],
    params["base_thickness"],
    align=(Align.CENTER, Align.CENTER, Align.MIN),
)

result = part
