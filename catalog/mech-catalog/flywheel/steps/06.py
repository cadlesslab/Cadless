from build123d import *

params = {
    "disc_radius": 140,  # 280 mm flywheel
    "disc_thickness": 32,
    "hub_radius": 45,
    "hub_top": 48,  # hub boss rises to Z=48, overlapping the disc from Z=30
    "hub_overlap": 2,
    "recess_inner_radius": 50,
    "recess_outer_radius": 115,
    "recess_depth": 12,  # web thinned to 20 mm between hub and rim
    "pilot_bore_radius": 15,  # 30 mm crank pilot bore
    "bolt_circle_radius": 32,  # 64 mm BCD, matches the crankshaft flange
    "bolt_hole_radius": 4.5,
    "bolt_count": 6,
    "rim_chamfer": 2,
}

# Flywheel disc: axis along Z, back face on the XY plane.
part = Cylinder(
    radius=params["disc_radius"],
    height=params["disc_thickness"],
    align=(Align.CENTER, Align.CENTER, Align.MIN),
)

# Central hub boss on the front face for the crankshaft flange.
hub_base = params["hub_top"] - params["disc_thickness"] + params["hub_overlap"]
part += Pos(0, 0, params["disc_thickness"] - params["hub_overlap"]) * Cylinder(
    radius=params["hub_radius"],
    height=hub_base,
    align=(Align.CENTER, Align.CENTER, Align.MIN),
)

# Annular recess in the front face: thins the web, keeping mass in the rim.
recess = Cylinder(
    radius=params["recess_outer_radius"],
    height=params["recess_depth"] + 1,
    align=(Align.CENTER, Align.CENTER, Align.MIN),
) - Cylinder(
    radius=params["recess_inner_radius"],
    height=params["recess_depth"] + 1,
    align=(Align.CENTER, Align.CENTER, Align.MIN),
)
part -= Pos(0, 0, params["disc_thickness"] - params["recess_depth"]) * recess

# Pilot bore through the centre for the crankshaft nose.
part -= Pos(0, 0, -1) * Cylinder(
    radius=params["pilot_bore_radius"],
    height=params["hub_top"] + 2,
    align=(Align.CENTER, Align.CENTER, Align.MIN),
)

# Six bolt holes through hub and disc on a 64 mm bolt circle.
for loc in PolarLocations(params["bolt_circle_radius"], params["bolt_count"]):
    hole = Cylinder(
        radius=params["bolt_hole_radius"],
        height=params["hub_top"] + 2,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    )
    part -= loc * Pos(0, 0, -1) * hole

# Chamfer the two sharp rim edges of the disc.
rim_edges = [e for e in part.edges().filter_by(GeomType.CIRCLE)
             if abs(e.radius - params["disc_radius"]) < 0.001]
part = chamfer(rim_edges, params["rim_chamfer"])

result = part
