from build123d import *

params = {
    "cover_length": 90,
    "cover_width": 30,
    "cover_height": 25,
    "wall": 2,
    "flange_length": 10,
    "flange_width": 20,
    "flange_thickness": 3,
    "flange_hole_radius": 1.7,  # M3 clearance, 3.4 mm diameter
}

# Cover blank centered in X/Y, resting on Z=0.
part = Box(
    params["cover_length"],
    params["cover_width"],
    params["cover_height"],
    align=(Align.CENTER, Align.CENTER, Align.MIN),
)

# Hollow from below, leaving walls and a roof.
cavity = Box(
    params["cover_length"] - 2 * params["wall"],
    params["cover_width"] - 2 * params["wall"],
    params["cover_height"] - params["wall"] + 1,
    align=(Align.CENTER, Align.CENTER, Align.MIN),
)
part -= Pos(0, 0, -1) * cavity

# Screw flanges at both ends, on the base plane.
flange_x = params["cover_length"] / 2 + params["flange_length"] / 2
for side in (1, -1):
    flange = Box(
        params["flange_length"],
        params["flange_width"],
        params["flange_thickness"],
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    )
    part += Pos(side * flange_x, 0, 0) * flange

# One screw hole through each flange.
for side in (1, -1):
    hole = Cylinder(
        radius=params["flange_hole_radius"],
        height=params["flange_thickness"] + 2,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    )
    part -= Pos(side * flange_x, 0, -1) * hole

result = part
