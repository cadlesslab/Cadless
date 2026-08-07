from build123d import *

params = {
    "plate_length": 60,
    "plate_depth": 50,
    "plate_thickness": 8,
}

# Mount plate centered in X/Y, resting on Z=0.
part = Box(
    params["plate_length"],
    params["plate_depth"],
    params["plate_thickness"],
    align=(Align.CENTER, Align.CENTER, Align.MIN),
)

result = part
