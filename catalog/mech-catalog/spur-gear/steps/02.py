from build123d import *

params = {
    "outer_radius": 30,
    "inner_radius": 8,
    "height": 12,
}

part = Cylinder(radius=params["outer_radius"], height=params["height"], align=(Align.CENTER, Align.CENTER, Align.MIN))
part -= Cylinder(radius=params["inner_radius"], height=params["height"], align=(Align.CENTER, Align.CENTER, Align.MIN))
result = part
