from build123d import *

params = {
    "width": 800,             # overall width (X), mm
    "depth": 300,             # overall depth (Y), mm
    "height": 1800,           # overall height (Z), mm
    "panel_thickness": 18,    # carcass panel thickness
    "back_thickness": 12,
    "shelf_bottoms": [370, 730, 1090, 1450],  # shelf underside heights (~360 mm pitch)
}

# Carcass: two sides plus top and bottom panels, fused into one frame.
t = params["panel_thickness"]
side_x = params["width"] / 2 - t / 2  # 391, outer faces at +/-400
part = Pos(side_x, 0, params["height"] / 2) * Box(t, params["depth"], params["height"])
part += Pos(-side_x, 0, params["height"] / 2) * Box(t, params["depth"], params["height"])
inner_width = params["width"] - 2 * t  # 764
part += Pos(0, 0, t / 2) * Box(inner_width, params["depth"], t)                      # bottom
part += Pos(0, 0, params["height"] - t / 2) * Box(inner_width, params["depth"], t)   # top

# Four fixed shelves between the sides.
for shelf_bottom in params["shelf_bottoms"]:
    part += Pos(0, 0, shelf_bottom + t / 2) * Box(inner_width, params["depth"], t)

# Back panel covering the full back face.
back_y = params["depth"] / 2 - params["back_thickness"] / 2  # 144, flush with the back
part += Pos(0, back_y, params["height"] / 2) * Box(
    params["width"], params["back_thickness"], params["height"])

result = part
