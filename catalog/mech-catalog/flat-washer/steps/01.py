from build123d import *

params = {"outer_dia": 32, "inner_dia": 13, "thickness": 4}
outer = Cylinder(radius=params["outer_dia"] / 2, height=params["thickness"])
inner = Cylinder(radius=params["inner_dia"] / 2, height=params["thickness"])
result = outer - inner
