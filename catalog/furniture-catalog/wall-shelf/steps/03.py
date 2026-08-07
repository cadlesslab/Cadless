from build123d import *

params = {
    "length": 900,             # shelf length (X), mm
    "depth": 250,              # shelf depth (Y), mm
    "board_thickness": 30,
    "upstand_thickness": 20,   # rear mounting rail thickness
    "upstand_height": 60,      # rail height above the board
    "screw_hole_diameter": 6,
    "screw_hole_spacing": 700, # hole centers, symmetric about X=0
}

# Shelf board. The wall side is the back edge at +Y.
part = Pos(0, 0, params["board_thickness"] / 2) * Box(
    params["length"], params["depth"], params["board_thickness"])

# Mounting upstand along the back edge, on top of the board.
upstand_y = params["depth"] / 2 - params["upstand_thickness"] / 2  # 115
upstand_z = params["board_thickness"] + params["upstand_height"] / 2  # 60
part += Pos(0, upstand_y, upstand_z) * Box(
    params["length"], params["upstand_thickness"], params["upstand_height"])

# Two horizontal screw holes through the upstand.
hole_x = params["screw_hole_spacing"] / 2  # 350
hole_z = params["board_thickness"] + params["upstand_height"] / 2  # 60, mid-upstand
for sx in (1, -1):
    part -= Pos(sx * hole_x, upstand_y, hole_z) * Rot(90, 0, 0) * Cylinder(
        radius=params["screw_hole_diameter"] / 2,
        height=params["upstand_thickness"] + 20)

result = part
