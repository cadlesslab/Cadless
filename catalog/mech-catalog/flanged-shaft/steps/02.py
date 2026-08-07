from build123d import *

params = {
    "shaft_radius": 6,
    "shaft_height": 80,
    "flange_radius": 20,
    "flange_height": 8,
}

shaft = Cylinder(radius=params["shaft_radius"], height=params["shaft_height"], align=(Align.CENTER, Align.CENTER, Align.MIN))
flange = Cylinder(radius=params["flange_radius"], height=params["flange_height"], align=(Align.CENTER, Align.CENTER, Align.MIN))
result = shaft + flange
