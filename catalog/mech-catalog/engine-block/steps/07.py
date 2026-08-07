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
    "bore_radius": 43,  # two 86 mm cylinder bores
    "bore_x": [-55, 55],  # 110 mm cylinder spacing
    "stud_hole_radius": 6,
    "stud_dx": 36,  # stud holes at +/-36 mm in X from each bore centre
    "stud_y": 52,
    "stud_bottom": 79,  # drilled from the deck through into the crankcase
    "cap_bolt_radius": 5,
    "cap_bolt_y": 40,  # blind holes flanking the crank tunnel
    "cap_bolt_depth": 20,
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

# Two cylinder bores from the deck down into the crankcase channel.
for x in params["bore_x"]:
    bore = Cylinder(
        radius=params["bore_radius"],
        height=params["height"] - params["channel_top"] + 2,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    )
    part -= Pos(x, 0, params["channel_top"] - 1) * bore

# Eight head-stud holes around the bores, deck through to the crankcase.
for bx in params["bore_x"]:
    for dx in (params["stud_dx"], -params["stud_dx"]):
        for sy in (params["stud_y"], -params["stud_y"]):
            stud = Cylinder(
                radius=params["stud_hole_radius"],
                height=params["height"] - params["stud_bottom"] + 1,
                align=(Align.CENTER, Align.CENTER, Align.MIN),
            )
            part -= Pos(bx + dx, sy, params["stud_bottom"]) * stud

# Six blind main-cap bolt holes in the bulkhead undersides.
for x in params["saddle_x"]:
    for sy in (params["cap_bolt_y"], -params["cap_bolt_y"]):
        cap_hole = Cylinder(
            radius=params["cap_bolt_radius"],
            height=params["cap_bolt_depth"] + 1,
            align=(Align.CENTER, Align.CENTER, Align.MIN),
        )
        part -= Pos(x, sy, params["saddle_bottom"] - 1) * cap_hole

result = part
