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

result = part
