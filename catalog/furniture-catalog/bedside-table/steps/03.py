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

# Hollow into an open-front shell.
front_face = part.faces().sort_by(Axis.Y)[0]
part = offset(part, amount=-params["wall_thickness"], openings=front_face)

# Drawer: a separate open-top box floating in the opening (second body).
drawer_y = -params["depth"] / 2 + params["drawer_depth"] / 2   # -20, front face flush at Y=-200
drawer_z = params["drawer_lift"] + params["drawer_height"] / 2  # 115
drawer = Pos(0, drawer_y, drawer_z) * Box(
    params["drawer_width"], params["drawer_depth"], params["drawer_height"])
drawer_top = drawer.faces().sort_by(Axis.Z)[-1]
drawer = offset(drawer, amount=-params["drawer_wall"], openings=drawer_top)

result = Compound(children=[part.solid(), drawer.solid()])
