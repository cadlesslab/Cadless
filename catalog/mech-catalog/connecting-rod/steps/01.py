from build123d import *

params = {
    "thickness": 22,  # rod thickness along Z
    "big_end_outer_radius": 31,
}

# Big-end boss: a cylinder at the origin, axis along Z, centred in thickness.
part = Cylinder(radius=params["big_end_outer_radius"], height=params["thickness"])

result = part
