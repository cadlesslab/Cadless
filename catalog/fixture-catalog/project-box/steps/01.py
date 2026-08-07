from build123d import *

params = {
    "outer_length": 80,
    "outer_width": 50,
    "outer_height": 22,
}

# Enclosure body blank: a solid block, base on Z=0.
part = Box(
    params["outer_length"],
    params["outer_width"],
    params["outer_height"],
    align=(Align.CENTER, Align.CENTER, Align.MIN),
)

result = part
