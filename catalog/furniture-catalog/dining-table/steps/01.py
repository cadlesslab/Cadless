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

result = part
