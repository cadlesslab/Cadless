from build123d import *

params = {
    "base_length": 80,
    "base_width": 60,
    "base_height": 8,
}

base = Box(
    params["base_length"],
    params["base_width"],
    params["base_height"],
    align=(Align.MIN, Align.MIN, Align.MIN),
)
result = base
