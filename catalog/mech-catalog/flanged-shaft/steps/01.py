from build123d import *

params = {
    "radius": 6,
    "height": 80,
}

result = Cylinder(radius=params["radius"], height=params["height"], align=(Align.CENTER, Align.CENTER, Align.MIN))
