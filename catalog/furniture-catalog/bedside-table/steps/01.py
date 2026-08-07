from build123d import *

params = {
    "width": 450,           # overall width (X), mm
    "depth": 400,           # overall depth (Y), mm; front face at -Y
    "height": 550,          # overall height (Z), mm
    "wall_thickness": 16,
    "drawer_width": 410,    # drawer outer width (4 mm side clearance each side)
    "drawer_depth": 360,    # drawer outer depth
    "drawer_height": 150,   # drawer outer height
    "drawer_wall": 12,      # drawer wall/base thickness
    "drawer_lift": 40,      # drawer underside height above the floor
}

# Bedside table blank. The front face is at -Y.
part = Pos(0, 0, params["height"] / 2) * Box(
    params["width"], params["depth"], params["height"])

result = part
