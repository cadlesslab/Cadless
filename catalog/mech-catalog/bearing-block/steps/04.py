from build123d import *

params = {
    "base_length": 100,
    "base_width": 40,
    "base_thickness": 12,
    "boss_length": 50,
    "boss_width": 40,
    "boss_height": 35,
    "boss_z_offset": 10,
    "bore_radius": 12.5,
    "bore_length": 60,
    "bore_z_height": 28,
    "hole_x_offset": 40,
    "hole_radius": 4,
    "hole_depth": 20,
    "hole_z_start": -1,
    "counterbore_radius": 7,
    "counterbore_depth": 5,
    "counterbore_z_start": 7,
}

# Base plate centered in X/Y, resting on Z=0.
base = Box(
    params["base_length"],
    params["base_width"],
    params["base_thickness"],
    align=(Align.CENTER, Align.CENTER, Align.MIN),
)

# Raised central boss carrying the bearing bore.
boss = Box(
    params["boss_length"],
    params["boss_width"],
    params["boss_height"],
    align=(Align.CENTER, Align.CENTER, Align.MIN),
)
boss = Pos(0, 0, params["boss_z_offset"]) * boss

part = base + boss

# Horizontal bearing bore along Y.
bore = Cylinder(radius=params["bore_radius"], height=params["bore_length"]).rotate(Axis.X, 90)
bore = Pos(0, 0, params["bore_z_height"]) * bore
part = part - bore

# Two counterbored fixing holes through the base plate near each end.
for x in (-params["hole_x_offset"], params["hole_x_offset"]):
    shank = Cylinder(
        radius=params["hole_radius"],
        height=params["hole_depth"],
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    )
    shank = Pos(x, 0, params["hole_z_start"]) * shank
    cbore = Cylinder(
        radius=params["counterbore_radius"],
        height=params["counterbore_depth"],
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    )
    cbore = Pos(x, 0, params["counterbore_z_start"]) * cbore
    part = part - shank - cbore

result = part
