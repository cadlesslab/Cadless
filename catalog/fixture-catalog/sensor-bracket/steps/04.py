from build123d import *

params = {
    "base_length": 40,
    "base_width": 30,
    "base_thickness": 4,
    "upright_thickness": 4,
    "upright_height": 50,
    "slot_radius": 2.1,  # M4 clearance, 4.2 mm slot width
    "slot_travel": 9.8,  # straight section between the end radii
    "slot_center_z": 30,
    "slot_dy": 8,
    "base_hole_radius": 2.1,  # M4 clearance, 4.2 mm diameter
    "base_hole_x": 28,
    "base_hole_dy": 8,
}

# Base plate from X=0 to X=40, centered in Y, resting on Z=0.
part = Box(
    params["base_length"],
    params["base_width"],
    params["base_thickness"],
    align=(Align.MIN, Align.CENTER, Align.MIN),
)

# Upright sensor face sharing the corner root with the base.
upright = Box(
    params["upright_thickness"],
    params["base_width"],
    params["upright_height"],
    align=(Align.MIN, Align.CENTER, Align.MIN),
)
part += upright

# Two rounded vertical slots through the upright.
for side in (1, -1):
    straight = Box(
        params["upright_thickness"] + 2,
        params["slot_radius"] * 2,
        params["slot_travel"],
    )
    part -= Pos(params["upright_thickness"] / 2, side * params["slot_dy"],
                params["slot_center_z"]) * straight
    for end in (1, -1):
        cap = Cylinder(
            radius=params["slot_radius"],
            height=params["upright_thickness"] + 2,
        ).rotate(Axis.Y, 90)
        part -= Pos(params["upright_thickness"] / 2, side * params["slot_dy"],
                    params["slot_center_z"] + end * params["slot_travel"] / 2) * cap

# Two mounting holes through the base.
for side in (1, -1):
    hole = Cylinder(
        radius=params["base_hole_radius"],
        height=params["base_thickness"] + 2,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    )
    part -= Pos(params["base_hole_x"], side * params["base_hole_dy"], -1) * hole

result = part
