from build123d import *

params = {
    "panel_length": 100,
    "panel_width": 70,
    "panel_thickness": 3,
}

# Panel plate centered in X/Y, resting on Z=0.
part = Box(
    params["panel_length"],
    params["panel_width"],
    params["panel_thickness"],
    align=(Align.CENTER, Align.CENTER, Align.MIN),
)

result = part
