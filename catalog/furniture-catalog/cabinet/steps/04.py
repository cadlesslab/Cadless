from build123d import *

params = {
    "width": 800,           # overall width (X), mm
    "depth": 400,           # overall depth (Y), mm; front face at -Y
    "height": 720,          # overall height (Z), mm
    "wall_thickness": 18,
    "shelf_thickness": 18,
    "toe_kick_height": 60,
    "toe_kick_depth": 60,
}

# Cabinet blank. The front face is at -Y.
part = Pos(0, 0, params["height"] / 2) * Box(
    params["width"], params["depth"], params["height"])

# Hollow the blank into an open-front shell.
front_face = part.faces().sort_by(Axis.Y)[0]
part = offset(part, amount=-params["wall_thickness"], openings=front_face)

# Mid-height shelf spanning the interior, flush with the front opening.
t = params["wall_thickness"]
inner_width = params["width"] - 2 * t                  # 764
inner_depth = params["depth"] - t                      # 382, open front to back wall
shelf_y = -params["depth"] / 2 + inner_depth / 2       # -9
shelf_z = params["height"] / 2 - params["shelf_thickness"] / 2  # 351: shelf spans Z=342..360
part += Pos(0, shelf_y, shelf_z) * Box(
    inner_width, inner_depth, params["shelf_thickness"])

# Toe-kick recess across the front bottom.
part -= Pos(0, -params["depth"] / 2 + params["toe_kick_depth"] / 2,
            params["toe_kick_height"] / 2) * Box(
    params["width"] + 10, params["toe_kick_depth"], params["toe_kick_height"])

result = part
