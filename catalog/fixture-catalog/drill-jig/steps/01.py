from build123d import *

params = {
    "body_length": 70,
    "body_width": 40,
    "body_thickness": 15,
}

# Jig body centered in X/Y, resting on Z=0.
part = Box(
    params["body_length"],
    params["body_width"],
    params["body_thickness"],
    align=(Align.CENTER, Align.CENTER, Align.MIN),
)

result = part
