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
    "pin_radius": 10.95,  # 22 mm nominal pin, 0.05 mm radial running clearance
    "pin_length": 84,  # full-floating pin, ends 0.75 mm inside the skirt surface
    "rod_length": 145,  # big-end to small-end centre distance
    "thickness": 22,  # rod thickness along its bore axes
    "big_end_outer_radius": 31,
    "big_end_bore_radius": 22.5,  # 45 mm crank pin
    "small_end_outer_radius": 17,
    "small_end_bore_radius": 11,  # 22 mm wrist pin
    "shank_length": 115,  # spans X=20..135, embedded into both bosses
    "shank_center_x": 77.5,
    "web_width": 6,
    "web_height": 18,
    "flange_width": 22,
    "flange_thickness": 4,
    "flange_z_offset": 7,  # flange centres at Z=+/-7
    "bolt_boss_size": (26, 16, 22),
    "bolt_boss_y": 32,  # bolt boss centres at Y=+/-32
    "bolt_hole_radius": 4.5,  # M8 clearance cap bolts
    "crank_angle_deg": 17,  # rod hangs 17 degrees off the cylinder axis
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


def build_wrist_pin(p):
    """Wrist pin on the piston's pin axis (along Y at Z=pin_z), with clearance."""
    pin = Cylinder(radius=p["pin_radius"], height=p["pin_length"]).rotate(Axis.X, 90)
    return Pos(0, 0, p["pin_z"]) * pin


def build_rod(p):
    """Engine Connecting Rod, identical to the catalog `connecting-rod` part.

    Authored in its own frame: big end at the origin, small end at X=rod_length,
    both bores along Z, thickness centred on Z=0."""
    # Big-end boss: a cylinder at the origin, axis along Z, centred in thickness.
    part = Cylinder(radius=p["big_end_outer_radius"], height=p["thickness"])

    # I-beam shank towards the small end: central web plus top and bottom flanges.
    web = Box(p["shank_length"], p["web_width"], p["web_height"])
    part += Pos(p["shank_center_x"], 0, 0) * web
    flange = Box(p["shank_length"], p["flange_width"], p["flange_thickness"])
    for side in (1, -1):
        part += Pos(p["shank_center_x"], 0, side * p["flange_z_offset"]) * flange

    # Small-end boss at the far end of the shank.
    part += Pos(p["rod_length"], 0, 0) * Cylinder(
        radius=p["small_end_outer_radius"], height=p["thickness"])

    # Two cap-bolt bosses flanking the big end.
    for side in (1, -1):
        part += Pos(0, side * p["bolt_boss_y"], 0) * Box(*p["bolt_boss_size"])

    # Big-end bore for the crank pin, straight through along Z.
    part -= Cylinder(radius=p["big_end_bore_radius"], height=p["thickness"] + 8)

    # Small-end bore for the wrist pin, straight through along Z.
    part -= Pos(p["rod_length"], 0, 0) * Cylinder(
        radius=p["small_end_bore_radius"], height=p["thickness"] + 8)

    # Cap bolt holes through both bolt bosses, axes along X (the rod axis).
    for side in (1, -1):
        bolt_hole = Cylinder(radius=p["bolt_hole_radius"], height=40).rotate(Axis.Y, 90)
        part -= Pos(0, side * p["bolt_boss_y"], 0) * bolt_hole

    return part


def place_rod(rod, p):
    """Hang the rod off the wrist pin at the crank angle.

    Right-to-left: stand the rod bores up along Y (Rot about X), move the
    small-end bore centre to the origin, swing the big end down and sideways to
    crank_angle_deg off the cylinder (-Z) axis (Rot about Y), then carry the
    small end onto the pin axis at (0, 0, pin_z)."""
    return (Pos(0, 0, p["pin_z"])
            * Rot(0, -(90 + p["crank_angle_deg"]), 0)
            * Pos(-p["rod_length"], 0, 0)
            * Rot(90, 0, 0)
            * rod)


piston = build_piston(params)
wrist_pin = build_wrist_pin(params)
rod = place_rod(build_rod(params), params)

# Positioned assembly: three disjoint bodies. The pin passes through both the
# piston pin bore and the rod small-end bore with 0.05 mm radial clearance; the
# rod's 22 mm small end sits centred in the 28 mm gap between the pin bosses
# and hangs 17 degrees off the cylinder axis (no joints/constraints). Each body
# is added as its bare Solid so the Compound's geometry summary sees them all.
result = Compound(children=[piston.solid(), wrist_pin.solid(), rod.solid()])
