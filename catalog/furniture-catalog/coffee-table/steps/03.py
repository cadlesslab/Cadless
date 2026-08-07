from build123d import *

params = {
    "top_length": 1100,      # tabletop length (X), mm
    "top_width": 600,        # tabletop width (Y), mm
    "top_thickness": 25,
    "table_height": 450,     # floor to top surface
    "leg_section": 50,       # square leg cross-section
    "leg_inset": 60,         # leg outer face inset from the top's edges
    "shelf_thickness": 15,
    "shelf_bottom": 130,     # shelf underside height
}

# Tabletop slab, top face at table height.
top_z = params["table_height"] - params["top_thickness"] / 2  # 437.5
part = Pos(0, 0, top_z) * Box(
    params["top_length"], params["top_width"], params["top_thickness"])

# Four square legs up to the tabletop's underside.
leg_height = params["table_height"] - params["top_thickness"]  # 425
leg_x = params["top_length"] / 2 - params["leg_inset"] - params["leg_section"] / 2  # 465
leg_y = params["top_width"] / 2 - params["leg_inset"] - params["leg_section"] / 2   # 215
for sx in (1, -1):
    for sy in (1, -1):
        part += Pos(sx * leg_x, sy * leg_y, leg_height / 2) * Box(
            params["leg_section"], params["leg_section"], leg_height)

# Lower shelf spanning to the legs' outer faces, fused through all four legs.
shelf_length = 2 * leg_x + params["leg_section"]  # 980, flush with the legs' outer faces
shelf_width = 2 * leg_y + params["leg_section"]   # 480
shelf_z = params["shelf_bottom"] + params["shelf_thickness"] / 2  # 137.5
part += Pos(0, 0, shelf_z) * Box(
    shelf_length, shelf_width, params["shelf_thickness"])

result = part
