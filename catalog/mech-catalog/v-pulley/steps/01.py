from build123d import *

params = {
    "pulley_radius": 35,
    "pulley_height": 25,
}

result = Cylinder(
    radius=params["pulley_radius"],
    height=params["pulley_height"],
    align=(Align.CENTER, Align.CENTER, Align.MIN),
)
