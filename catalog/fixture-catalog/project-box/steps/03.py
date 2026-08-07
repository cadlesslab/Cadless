from build123d import *

params = {
    "outer_length": 80,
    "outer_width": 50,
    "outer_height": 22,
    "wall": 3,
    "floor": 2.5,
    "boss_radius": 4,
    "boss_x": 34,
    "boss_y": 19,
}

# Enclosure body blank: a solid block, base on Z=0.
part = Box(
    params["outer_length"],
    params["outer_width"],
    params["outer_height"],
    align=(Align.CENTER, Align.CENTER, Align.MIN),
)

# Hollow the interior, leaving walls and a floor; open at the top.
cavity = Box(
    params["outer_length"] - 2 * params["wall"],
    params["outer_width"] - 2 * params["wall"],
    params["outer_height"],
    align=(Align.CENTER, Align.CENTER, Align.MIN),
)
part -= Pos(0, 0, params["floor"]) * cavity

# Four corner screw bosses, fused into the cavity walls, floor to rim.
boss_height = params["outer_height"] - params["floor"]
for x in (-params["boss_x"], params["boss_x"]):
    for y in (-params["boss_y"], params["boss_y"]):
        boss = Cylinder(
            radius=params["boss_radius"],
            height=boss_height,
            align=(Align.CENTER, Align.CENTER, Align.MIN),
        )
        part += Pos(x, y, params["floor"]) * boss

result = part
