from build123d import *

params = {
    "plate_length": 60,
    "plate_depth": 50,
    "plate_thickness": 8,
    "channel_width": 35.4,  # 0.4 mm clearance over a 35 mm TS35 rail
    "channel_depth": 4,
    "lip_width": 2,
    "lip_height": 1.5,
    "hole_radius": 2.1,  # M4 clearance, 4.2 mm diameter
    "hole_dy": 18,
    "chamfer_length": 1,
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

# Retaining lips hooking over the rail flanges, one per foot.
lip_x = params["channel_width"] / 2 - params["lip_width"] / 2
for side in (1, -1):
    lip = Box(
        params["lip_width"],
        params["plate_depth"],
        params["lip_height"],
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    )
    part += Pos(side * lip_x, 0, 0) * lip

# Two M4 device mounting holes through the plate.
for side in (1, -1):
    hole = Cylinder(
        radius=params["hole_radius"],
        height=params["plate_thickness"] + 2,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    )
    part -= Pos(0, side * params["hole_dy"], -1) * hole

# Break the top edges.
top_edges = part.edges().filter_by(GeomType.LINE).group_by(Axis.Z)[-1]
part = chamfer(top_edges, length=params["chamfer_length"])

result = part
