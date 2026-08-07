from build123d import *

params = {
    "hinge_length": 60,  # barrel length along Z
    "leaf_width": 30,  # plate reach measured from the barrel axis
    "leaf_thickness": 3,
    "plate_inner_x": 3,  # plate starts inside the 5 mm barrel so they merge
    "barrel_radius": 5,  # 10 mm knuckle barrel
    "pin_bore_radius": 2.5,  # 5 mm hinge pin
    "knuckle_clearance": 0.1,  # axial gap each side where knuckles alternate
    "knuckle_edges": [0, 12, 24, 36, 48, 60],  # five 12 mm knuckle bands
    "screw_hole_radius": 2.5,  # M5 clearance screw holes
    "screw_holes": [(13, 45), (25, 15)],  # (x, z) hole centres on the plate
}


def build_leaf(p, knuckle_bands, notch_bands):
    """One hinge leaf: flat plate fused to its alternating barrel knuckles.

    Authored in the leaf's own frame: barrel axis on Z, plate extending +X with
    its mid-plane on Y=0. ``knuckle_bands`` picks which of the five 12 mm bands
    along the barrel belong to this leaf; each band is shrunk by the axial
    clearance so mating knuckles never touch. ``notch_bands`` are the mating
    leaf's bands: the plate is cut back around them with radial + axial
    clearance so the assembled leaves stay disjoint."""
    edges = p["knuckle_edges"]
    gap = p["knuckle_clearance"]

    # Plate spanning the full hinge length, overlapping the barrel to fuse.
    part = Box(
        p["leaf_width"] - p["plate_inner_x"],
        p["leaf_thickness"],
        p["hinge_length"],
        align=(Align.MIN, Align.CENTER, Align.MIN),
    ).move(Pos(p["plate_inner_x"], 0, 0))

    # This leaf's knuckles: barrel segments with axial clearance at shared edges.
    for band in knuckle_bands:
        z0, z1 = edges[band], edges[band + 1]
        if band > 0:
            z0 += gap
        if band < len(edges) - 2:
            z1 -= gap
        part += Pos(0, 0, z0) * Cylinder(
            radius=p["barrel_radius"], height=z1 - z0,
            align=(Align.CENTER, Align.CENTER, Align.MIN))

    # Notch the plate around the mating leaf's knuckle bands.
    for band in notch_bands:
        z0, z1 = edges[band], edges[band + 1]
        part -= Pos(0, 0, z0) * Cylinder(
            radius=p["barrel_radius"] + gap, height=z1 - z0,
            align=(Align.CENTER, Align.CENTER, Align.MIN))

    # Pin bore straight through this leaf's knuckles.
    part -= Pos(0, 0, -1) * Cylinder(
        radius=p["pin_bore_radius"], height=p["hinge_length"] + 2,
        align=(Align.CENTER, Align.CENTER, Align.MIN))

    # Two countersink-free screw holes through the plate.
    for x, z in p["screw_holes"]:
        hole = Cylinder(radius=p["screw_hole_radius"],
                        height=p["leaf_thickness"] + 2).rotate(Axis.X, 90)
        part -= Pos(x, 0, z) * hole

    return part


# Fixed leaf: the three outer knuckle bands (bottom, middle, top).
leaf_fixed = build_leaf(params, knuckle_bands=[0, 2, 4], notch_bands=[1, 3])

result = leaf_fixed
