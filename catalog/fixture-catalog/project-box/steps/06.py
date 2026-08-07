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
    "pilot_radius": 1.25,
    "pilot_depth": 16,
    "lid_thickness": 3,
    "lip_depth": 2,
    "lip_clearance": 0.5,
    "lid_gap": 8,
    "lid_hole_radius": 1.35,
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

# Blind self-tap pilot holes down the boss centres.
for x in (-params["boss_x"], params["boss_x"]):
    for y in (-params["boss_y"], params["boss_y"]):
        pilot = Cylinder(
            radius=params["pilot_radius"],
            height=params["pilot_depth"] + 1,
            align=(Align.CENTER, Align.CENTER, Align.MAX),
        )
        part -= Pos(x, y, params["outer_height"] + 1) * pilot

# Lid: a flat plate with an inner locating lip, exploded above the body.
lid_z = params["outer_height"] + params["lid_gap"]
lid = Pos(0, 0, lid_z) * Box(
    params["outer_length"],
    params["outer_width"],
    params["lid_thickness"],
    align=(Align.CENTER, Align.CENTER, Align.MIN),
)
lip = Pos(0, 0, lid_z - params["lip_depth"]) * Box(
    params["outer_length"] - 2 * params["wall"] - 2 * params["lip_clearance"],
    params["outer_width"] - 2 * params["wall"] - 2 * params["lip_clearance"],
    params["lip_depth"],
    align=(Align.CENTER, Align.CENTER, Align.MIN),
)
lid += lip

# M2.5 clearance holes through the lid and lip, over each boss.
for x in (-params["boss_x"], params["boss_x"]):
    for y in (-params["boss_y"], params["boss_y"]):
        hole = Cylinder(
            radius=params["lid_hole_radius"],
            height=params["lip_depth"] + params["lid_thickness"] + 2,
            align=(Align.CENTER, Align.CENTER, Align.MIN),
        )
        lid -= Pos(x, y, lid_z - params["lip_depth"] - 1) * hole

result = Compound(children=[part.solid(), lid.solid()])
