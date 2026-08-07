from build123d import *

params = {
    "outer_radius": 42.75,  # 85.5 mm piston diameter for an 86 mm cylinder bore
    "height": 62,
    "groove_root_radius": 38.25,  # 4.5 mm deep ring grooves
    "groove_height": 3,
    "groove_z_bottoms": [52, 46, 40],  # two compression rings + one oil ring
    "interior_radius": 36.75,  # skirt wall 6 mm thick
    "interior_top": 50,  # leaves a 12 mm thick crown
    "boss_radius": 16,
    "boss_y_inner": 14,  # 28 mm gap between the bosses for the rod small end
    "boss_y_outer": 38,
    "pin_z": 24,  # wrist-pin axis height above the skirt bottom
    "pin_bore_radius": 11,  # 22 mm wrist pin
    "crown_chamfer": 1.0,
}


def build_piston(p):
    """Engine Piston, identical to the catalog `piston` part (86 mm family)."""
    # Piston blank: a solid cylinder, crown at the top (Z=62), skirt down to Z=0.
    part = Cylinder(
        radius=p["outer_radius"],
        height=p["height"],
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    )

    # Three ring grooves: annular cuts into the outer surface near the crown.
    for z in p["groove_z_bottoms"]:
        ring = Cylinder(
            radius=p["outer_radius"] + 1,
            height=p["groove_height"],
            align=(Align.CENTER, Align.CENTER, Align.MIN),
        ) - Cylinder(
            radius=p["groove_root_radius"],
            height=p["groove_height"],
            align=(Align.CENTER, Align.CENTER, Align.MIN),
        )
        part -= Pos(0, 0, z) * ring

    # Hollow the skirt from below, leaving the crown solid.
    part -= Pos(0, 0, -1) * Cylinder(
        radius=p["interior_radius"],
        height=p["interior_top"] + 1,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    )

    # Two wrist-pin bosses inside the skirt, one on each wall, axis along Y.
    boss_length = p["boss_y_outer"] - p["boss_y_inner"]
    boss_center = (p["boss_y_inner"] + p["boss_y_outer"]) / 2
    for side in (1, -1):
        boss = Cylinder(radius=p["boss_radius"], height=boss_length).rotate(Axis.X, 90)
        part += Pos(0, side * boss_center, p["pin_z"]) * boss

    # Wrist-pin bore straight through both bosses and skirt walls, axis along Y.
    pin_bore = Cylinder(radius=p["pin_bore_radius"], height=100).rotate(Axis.X, 90)
    part -= Pos(0, 0, p["pin_z"]) * pin_bore

    # Break the crown's sharp outer edge with a 1 mm chamfer.
    crown_edges = part.edges().filter_by(GeomType.CIRCLE).group_by(Axis.Z)[-1]
    return chamfer(crown_edges, p["crown_chamfer"])


piston = build_piston(params)

result = piston
