from build123d import *

params = {
    "leg_length": 60,
    "leg_width": 40,
    "leg_thickness": 5,
}

# Horizontal leg from X=0 to X=60, centered in Y, resting on Z=0.
part = Box(
    params["leg_length"],
    params["leg_width"],
    params["leg_thickness"],
    align=(Align.MIN, Align.CENTER, Align.MIN),
)

result = part
