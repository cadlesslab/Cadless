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

result = part
