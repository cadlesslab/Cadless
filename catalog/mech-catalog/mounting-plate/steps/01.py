from build123d import *

params = {"length": 80, "width": 60, "thickness": 6, "hole_dia": 6, "edge_margin": 10}
plate = Box(params["length"], params["width"], params["thickness"])
hx = params["length"] / 2 - params["edge_margin"]
hy = params["width"] / 2 - params["edge_margin"]
r = params["hole_dia"] / 2
h = params["thickness"]
holes = (
    Pos(hx, hy, 0) * Cylinder(radius=r, height=h)
    + Pos(-hx, hy, 0) * Cylinder(radius=r, height=h)
    + Pos(hx, -hy, 0) * Cylinder(radius=r, height=h)
    + Pos(-hx, -hy, 0) * Cylinder(radius=r, height=h)
)
result = plate - holes
