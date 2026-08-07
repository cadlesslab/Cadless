from build123d import *

params = {
    "width": 1400,          # overall width (X), mm
    "depth": 400,           # overall depth (Y), mm; front face at -Y
    "height": 450,          # overall height (Z), mm
    "wall_thickness": 18,
    "divider_thickness": 18,
    "cable_hole_diameter": 60,
    "cable_hole_x": 350,    # hole centers at X=+/-350
    "cable_hole_z": 350,    # hole center height
}

# TV stand blank. The front face is at -Y.
part = Pos(0, 0, params["height"] / 2) * Box(
    params["width"], params["depth"], params["height"])

# Hollow into an open-front shell.
front_face = part.faces().sort_by(Axis.Y)[0]
part = offset(part, amount=-params["wall_thickness"], openings=front_face)

# Center divider splitting the bay into two compartments.
t = params["wall_thickness"]
inner_depth = params["depth"] - t                    # 382, open front to back wall
inner_height = params["height"] - 2 * t              # 414
divider_y = -params["depth"] / 2 + inner_depth / 2   # -9
part += Pos(0, divider_y, params["height"] / 2) * Box(
    params["divider_thickness"], inner_depth, inner_height)

result = part
