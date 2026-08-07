from build123d import *

params = {
    "length": 300,  # along the crank axis (X)
    "width": 160,
    "height": 220,  # deck face at Z=220
    "channel_width": 120,  # open-bottomed crankcase channel
    "channel_top": 90,
    "saddle_thickness": 20,
    "saddle_x": [-110, 0, 110],  # three main-bearing bulkheads
    "saddle_bottom": 50,
    "crank_tunnel_radius": 28,  # clears the 50 mm main journals
    "crank_z": 50,  # crank axis height
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

# Three main-bearing bulkheads spanning the channel, hung from its ceiling.
for x in params["saddle_x"]:
    saddle = Box(
        params["saddle_thickness"], params["channel_width"],
        params["channel_top"] - params["saddle_bottom"],
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    )
    part += Pos(x, 0, params["saddle_bottom"]) * saddle

# Crank tunnel: a half-open bore along X notching a saddle into each bulkhead.
tunnel = Cylinder(
    radius=params["crank_tunnel_radius"], height=params["length"] + 10,
).rotate(Axis.Y, 90)
part -= Pos(0, 0, params["crank_z"]) * tunnel

result = part
