from build123d import *

params = {
    "top_length": 1600,       # tabletop length (X), mm
    "top_width": 900,         # tabletop width (Y), mm
    "top_thickness": 30,
    "table_height": 750,      # floor to top surface
    "leg_section": 60,        # square leg cross-section
    "leg_inset": 80,          # leg outer face inset from the top's edges
    "apron_thickness": 20,
    "apron_height": 100,
    "top_corner_fillet": 20,
}

# Tabletop: a slab centered in X/Y with its top face at table height.
top_z = params["table_height"] - params["top_thickness"] / 2  # slab center Z = 735
part = Pos(0, 0, top_z) * Box(
    params["top_length"], params["top_width"], params["top_thickness"])

# Four square legs, outer faces inset from the tabletop edges.
leg_height = params["table_height"] - params["top_thickness"]  # 720, to the top's underside
leg_x = params["top_length"] / 2 - params["leg_inset"] - params["leg_section"] / 2  # 690
leg_y = params["top_width"] / 2 - params["leg_inset"] - params["leg_section"] / 2   # 340
for sx in (1, -1):
    for sy in (1, -1):
        part += Pos(sx * leg_x, sy * leg_y, leg_height / 2) * Box(
            params["leg_section"], params["leg_section"], leg_height)

result = part
