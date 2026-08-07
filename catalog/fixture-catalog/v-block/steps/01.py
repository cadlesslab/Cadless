from build123d import *

params = {
    "block_width": 60,
    "block_depth": 40,
    "block_height": 40,
}

# Block blank centered in X/Y, resting on Z=0.
part = Box(
    params["block_width"],
    params["block_depth"],
    params["block_height"],
    align=(Align.CENTER, Align.CENTER, Align.MIN),
)

result = part
