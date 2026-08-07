from build123d import *

params = {
    "rim_radius": 30,
    "height": 12,
    "bore_radius": 8,
    "keyway_width": 5,
    "keyway_depth": 4,
    "keyway_y_offset": 7,
    "num_teeth": 18,
    "tooth_radial_length": 7,
    "tooth_tangential_width": 8,
    "tooth_center_radius": 31.5,
}

part = Cylinder(radius=params["rim_radius"], height=params["height"], align=(Align.CENTER, Align.CENTER, Align.MIN))
part -= Cylinder(radius=params["bore_radius"], height=params["height"], align=(Align.CENTER, Align.CENTER, Align.MIN))
keyway = Box(params["keyway_width"], params["keyway_depth"], params["height"], align=(Align.CENTER, Align.MIN, Align.MIN))
part -= Pos(0, params["keyway_y_offset"], 0) * keyway

for loc in PolarLocations(0, params["num_teeth"]):
    tooth = Pos(params["tooth_center_radius"], 0, 0) * Box(params["tooth_radial_length"], params["tooth_tangential_width"], params["height"], align=(Align.CENTER, Align.CENTER, Align.MIN))
    part += loc * tooth

result = part
