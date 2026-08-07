from build123d import *

params = {
    "base_length": 100,
    "base_width": 40,
    "base_thickness": 12,
    "boss_length": 50,
    "boss_width": 40,
    "boss_height": 35,
    "boss_z_offset": 10,
    "bearing_radius": 12.5,
    "bearing_bore_length": 60,
    "bearing_axis_height": 28,
    "fixing_hole_x_offset": 40,
    "shank_radius": 4,
    "shank_height": 20,
    "shank_z_offset": -1,
    "cbore_radius": 7,
    "cbore_height": 5,
    "cbore_z_offset": 7,
    "chamfer_length": 2,
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
bore = Cylinder(radius=params["bearing_radius"], height=params["bearing_bore_length"]).rotate(Axis.X, 90)
bore = Pos(0, 0, params["bearing_axis_height"]) * bore
part = part - bore

# Two counterbored fixing holes through the base plate near each end.
for x in (-params["fixing_hole_x_offset"], params["fixing_hole_x_offset"]):
    shank = Cylinder(
        radius=params["shank_radius"],
        height=params["shank_height"],
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    )
    shank = Pos(x, 0, params["shank_z_offset"]) * shank
    cbore = Cylinder(
        radius=params["cbore_radius"],
        height=params["cbore_height"],
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    )
    cbore = Pos(x, 0, params["cbore_z_offset"]) * cbore
    part = part - shank - cbore

# Add chamfers to the top edges of the boss.
top_edges = part.edges().filter_by(GeomType.LINE).group_by(Axis.Z)[-1]
result = chamfer(top_edges, length=params["chamfer_length"])
