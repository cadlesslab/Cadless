from build123d import *

params = {
    "outer_radius": 30,
    "height": 12,
    "bore_radius": 8,
    "keyway_width": 5,
    "keyway_depth": 4,
    "keyway_offset_y": 7,
}

part = Cylinder(radius=params["outer_radius"], height=params["height"], align=(Align.CENTER, Align.CENTER, Align.MIN))
part -= Cylinder(radius=params["bore_radius"], height=params["height"], align=(Align.CENTER, Align.CENTER, Align.MIN))
keyway = Box(params["keyway_width"], params["keyway_depth"], params["height"], align=(Align.CENTER, Align.MIN, Align.MIN))
part -= Pos(0, params["keyway_offset_y"], 0) * keyway
result = part
