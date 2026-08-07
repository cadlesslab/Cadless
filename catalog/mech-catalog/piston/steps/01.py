from build123d import *

params = {
    "outer_radius": 42.75,  # 85.5 mm piston diameter for an 86 mm cylinder bore
    "height": 62,
}

# Piston blank: a solid cylinder, crown at the top (Z=62), skirt down to Z=0.
part = Cylinder(
    radius=params["outer_radius"],
    height=params["height"],
    align=(Align.CENTER, Align.CENTER, Align.MIN),
)

result = part
