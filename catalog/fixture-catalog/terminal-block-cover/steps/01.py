from build123d import *

params = {
    "cover_length": 90,
    "cover_width": 30,
    "cover_height": 25,
}

# Cover blank centered in X/Y, resting on Z=0.
part = Box(
    params["cover_length"],
    params["cover_width"],
    params["cover_height"],
    align=(Align.CENTER, Align.CENTER, Align.MIN),
)

result = part
