from build123d import *

params = {
    "plate_length": 92,
    "plate_width": 61,
    "plate_thickness": 4,
    "corner_radius": 6,
    "boss_radius": 3.5,
    "boss_height": 5,
    "hole_dx": 29,  # 58 mm between centres in X
    "hole_dy": 24.5,  # 49 mm between centres in Y
    "hole_radius": 1.35,  # M2.5 clearance, 2.7 mm diameter
    "slot_length": 30,
    "slot_width": 14,
}

# Base plate centered in X/Y, resting on Z=0.
part = Box(
    params["plate_length"],
    params["plate_width"],
    params["plate_thickness"],
    align=(Align.CENTER, Align.CENTER, Align.MIN),
)

# Round the plate corners.
corner_edges = part.edges().filter_by(Axis.Z)
part = fillet(corner_edges, radius=params["corner_radius"])

# Four standoff bosses on the Raspberry Pi 58 x 49 mm hole pattern.
for x in (-params["hole_dx"], params["hole_dx"]):
    for y in (-params["hole_dy"], params["hole_dy"]):
        boss = Cylinder(
            radius=params["boss_radius"],
            height=params["boss_height"],
            align=(Align.CENTER, Align.CENTER, Align.MIN),
        )
        part += Pos(x, y, params["plate_thickness"]) * boss

# M2.5 clearance holes through bosses and plate.
for x in (-params["hole_dx"], params["hole_dx"]):
    for y in (-params["hole_dy"], params["hole_dy"]):
        hole = Cylinder(
            radius=params["hole_radius"],
            height=params["plate_thickness"] + params["boss_height"] + 2,
            align=(Align.CENTER, Align.CENTER, Align.MIN),
        )
        part -= Pos(x, y, -1) * hole

# Central cable pass-through slot.
slot = Box(
    params["slot_length"],
    params["slot_width"],
    params["plate_thickness"] + 2,
    align=(Align.CENTER, Align.CENTER, Align.MIN),
)
part -= Pos(0, 0, -1) * slot

result = part
