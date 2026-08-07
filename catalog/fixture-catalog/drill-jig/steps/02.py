from build123d import *

params = {
    "body_length": 70,
    "body_width": 40,
    "body_thickness": 15,
    "fence_width": 8,
    "fence_height": 12,
    "fence_drop": 8,  # how far the fence hangs below the base
}

# Jig body centered in X/Y, resting on Z=0.
part = Box(
    params["body_length"],
    params["body_width"],
    params["body_thickness"],
    align=(Align.CENTER, Align.CENTER, Align.MIN),
)

# Registration fence hooked over the workpiece edge.
fence = Box(
    params["body_length"],
    params["fence_width"],
    params["fence_height"],
    align=(Align.CENTER, Align.MAX, Align.MIN),
)
part += Pos(0, -params["body_width"] / 2, -params["fence_drop"]) * fence

result = part
