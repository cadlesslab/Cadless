from build123d import *

params = {"outer_dia": 40, "height": 22, "wall_thickness": 3, "inner_dia": 32}
body = Cylinder(radius=params["outer_dia"] / 2, height=params["height"])
pocket = Pos(0, 0, params["wall_thickness"] / 2) * Cylinder(
    radius=params["inner_dia"] / 2, height=params["height"] - params["wall_thickness"]
)
result = body - pocket
