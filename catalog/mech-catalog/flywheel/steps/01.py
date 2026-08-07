from build123d import *

params = {
    "disc_radius": 140,  # 280 mm flywheel
    "disc_thickness": 32,
}

# Flywheel disc: axis along Z, back face on the XY plane.
part = Cylinder(
    radius=params["disc_radius"],
    height=params["disc_thickness"],
    align=(Align.CENTER, Align.CENTER, Align.MIN),
)

result = part
