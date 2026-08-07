from build123d import *

params = {
    "base_length": 100,
    "base_width": 40,
    "base_height": 12,
    "boss_length": 50,
    "boss_width": 40,
    "boss_height": 35,
    "boss_z_offset": 10,
    "bore_radius": 12.5,
    "bore_cutter_length": 60,
    "bore_z_position": 28,
}

# Base plate centered in X/Y, resting on Z=0.
base = Box(
    params["base_length"],
    params["base_width"],
    params["base_height"],
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

# Bore a hole horizontally through the boss along Y.
bore = Cylinder(radius=params["bore_radius"], height=params["bore_cutter_length"]).rotate(Axis.X, 90)
bore = Pos(0, 0, params["bore_z_position"]) * bore

result = part - bore
