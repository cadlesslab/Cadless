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

# Four aprons spanning between the legs, flush under the tabletop.
apron_top = params["table_height"] - params["top_thickness"]  # 720
apron_z = apron_top - params["apron_height"] / 2  # 670: aprons span Z=620..720
long_span = 2 * leg_x - params["leg_section"]   # 1320, between leg inner faces
short_span = 2 * leg_y - params["leg_section"]  # 620
for sy in (1, -1):
    part += Pos(0, sy * leg_y, apron_z) * Box(
        long_span, params["apron_thickness"], params["apron_height"])
for sx in (1, -1):
    part += Pos(sx * leg_x, 0, apron_z) * Box(
        params["apron_thickness"], short_span, params["apron_height"])

result = part
