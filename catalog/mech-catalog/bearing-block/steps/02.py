from build123d import *

params = {
    "base_length": 100,
    "base_width": 40,
    "base_height": 12,
    "boss_length": 50,
    "boss_width": 40,
    "boss_height": 35,
    "boss_z_offset": 10,
}

# Base plate: centered in X/Y, resting on Z=0.
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

result = base + boss
