from build123d import *

params = {
    "plate_length": 60,
    "plate_depth": 50,
    "plate_thickness": 8,
    "channel_width": 35.4,  # 0.4 mm clearance over a 35 mm TS35 rail
    "channel_depth": 4,
}

# Mount plate centered in X/Y, resting on Z=0.
part = Box(
    params["plate_length"],
    params["plate_depth"],
    params["plate_thickness"],
    align=(Align.CENTER, Align.CENTER, Align.MIN),
)

# DIN rail channel recessed into the underside, full depth in Y.
channel = Box(
    params["channel_width"],
    params["plate_depth"] + 2,
    params["channel_depth"] + 1,
    align=(Align.CENTER, Align.CENTER, Align.MIN),
)
part -= Pos(0, 0, -1) * channel

result = part
