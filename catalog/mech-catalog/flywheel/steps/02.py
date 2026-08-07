from build123d import *

params = {
    "disc_radius": 140,  # 280 mm flywheel
    "disc_thickness": 32,
    "hub_radius": 45,
    "hub_top": 48,  # hub boss rises to Z=48, overlapping the disc from Z=30
    "hub_overlap": 2,
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

result = part
