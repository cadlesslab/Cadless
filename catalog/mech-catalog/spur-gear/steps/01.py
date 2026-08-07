from build123d import *

params = {
    "radius": 30,
    "height": 12,
}

result = Cylinder(radius=params["radius"], height=params["height"], align=(Align.CENTER, Align.CENTER, Align.MIN))
