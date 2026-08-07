from build123d import *

params = {
    "outer_radius": 42.75,  # 85.5 mm piston diameter for an 86 mm cylinder bore
    "height": 62,
    "groove_root_radius": 38.25,  # 4.5 mm deep ring grooves
    "groove_height": 3,
    "groove_z_bottoms": [52, 46, 40],  # two compression rings + one oil ring
    "interior_radius": 36.75,  # skirt wall 6 mm thick
    "interior_top": 50,  # leaves a 12 mm thick crown
}

# Piston blank: a solid cylinder, crown at the top (Z=62), skirt down to Z=0.
part = Cylinder(
    radius=params["outer_radius"],
    height=params["height"],
    align=(Align.CENTER, Align.CENTER, Align.MIN),
)

# Three ring grooves: annular cuts into the outer surface near the crown.
for z in params["groove_z_bottoms"]:
    ring = Cylinder(
        radius=params["outer_radius"] + 1,
        height=params["groove_height"],
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    ) - Cylinder(
        radius=params["groove_root_radius"],
        height=params["groove_height"],
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    )
    part -= Pos(0, 0, z) * ring

# Hollow the skirt from below, leaving the crown solid.
part -= Pos(0, 0, -1) * Cylinder(
    radius=params["interior_radius"],
    height=params["interior_top"] + 1,
    align=(Align.CENTER, Align.CENTER, Align.MIN),
)

result = part
