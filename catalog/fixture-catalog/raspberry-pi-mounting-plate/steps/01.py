from build123d import *

params = {
    "plate_length": 92,
    "plate_width": 61,
    "plate_thickness": 4,
}

# Base plate centered in X/Y, resting on Z=0.
part = Box(
    params["plate_length"],
    params["plate_width"],
    params["plate_thickness"],
    align=(Align.CENTER, Align.CENTER, Align.MIN),
)

result = part
