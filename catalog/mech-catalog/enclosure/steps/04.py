from build123d import *

params = {
    "box_length": 80,
    "box_width": 60,
    "box_height": 30,
    "wall_thickness": 3,
    "boss_diameter": 8,
    "boss_height": 18.0,
    "boss_inset_x": 30,
    "boss_inset_y": 20,
    "pilot_hole_diameter": 3,
    "pilot_hole_extra_depth": 2,
}

with BuildPart() as p:
    Box(params["box_length"], params["box_width"], params["box_height"])
    offset(amount=-params["wall_thickness"], openings=p.faces().sort_by(Axis.Z)[-1])

result = p.part

floor_z = -params["box_height"] / 2 + params["wall_thickness"]

for x in (-params["boss_inset_x"], params["boss_inset_x"]):
    for y in (-params["boss_inset_y"], params["boss_inset_y"]):
        result += Pos(x, y, floor_z) * Cylinder(
            radius=params["boss_diameter"] / 2,
            height=params["boss_height"],
            align=(Align.CENTER, Align.CENTER, Align.MIN),
        )

hole_depth = params["boss_height"] + params["pilot_hole_extra_depth"]

for x in (-params["boss_inset_x"], params["boss_inset_x"]):
    for y in (-params["boss_inset_y"], params["boss_inset_y"]):
        result -= Pos(x, y, floor_z) * Cylinder(
            radius=params["pilot_hole_diameter"] / 2,
            height=hole_depth,
            align=(Align.CENTER, Align.CENTER, Align.MIN),
        )
