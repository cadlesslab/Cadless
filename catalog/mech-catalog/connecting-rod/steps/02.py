from build123d import *

params = {
    "thickness": 22,  # rod thickness along Z
    "big_end_outer_radius": 31,
    "shank_length": 115,  # spans X=20..135, embedded into both bosses
    "shank_center_x": 77.5,
    "web_width": 6,
    "web_height": 18,
    "flange_width": 22,
    "flange_thickness": 4,
    "flange_z_offset": 7,  # flange centres at Z=+/-7
}

# Big-end boss: a cylinder at the origin, axis along Z, centred in thickness.
part = Cylinder(radius=params["big_end_outer_radius"], height=params["thickness"])

# I-beam shank towards the small end: central web plus top and bottom flanges.
web = Box(params["shank_length"], params["web_width"], params["web_height"])
part += Pos(params["shank_center_x"], 0, 0) * web
flange = Box(params["shank_length"], params["flange_width"], params["flange_thickness"])
for side in (1, -1):
    part += Pos(params["shank_center_x"], 0, side * params["flange_z_offset"]) * flange

result = part
