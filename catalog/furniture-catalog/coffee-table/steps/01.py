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

result = part
