from build123d import *

params = {"base_length": 60, "base_width": 40, "thickness": 6, "wall_height": 45, "hole_dia": 6}
base = Box(params["base_length"], params["base_width"], params["thickness"])
wall = Pos(
    -(params["base_length"] - params["thickness"]) / 2,
    0,
    (params["wall_height"] - params["thickness"]) / 2 + params["thickness"] / 2,
) * Box(params["thickness"], params["base_width"], params["wall_height"])
hole = Pos(params["base_length"] / 4, 0, 0) * Cylinder(
    radius=params["hole_dia"] / 2, height=params["thickness"]
)
result = base + wall - hole
