from build123d import *

params = {
    "length": 300,  # along the crank axis (X)
    "width": 160,
    "height": 220,  # deck face at Z=220
    "channel_width": 120,  # open-bottomed crankcase channel
    "channel_top": 90,
}

# Block blank: base on the XY plane, deck face on top at Z=220.
part = Box(
    params["length"], params["width"], params["height"],
    align=(Align.CENTER, Align.CENTER, Align.MIN),
)

# Open-bottomed crankcase channel, running the full length of the block.
channel = Box(
    params["length"] + 2, params["channel_width"], params["channel_top"] + 1,
    align=(Align.CENTER, Align.CENTER, Align.MIN),
)
part -= Pos(0, 0, -1) * channel

result = part
