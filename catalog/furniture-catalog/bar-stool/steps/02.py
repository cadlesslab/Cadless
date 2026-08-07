from build123d import *

params = {
    "seat_diameter": 350,
    "seat_thickness": 30,
    "stool_height": 750,   # floor to seat surface
    "leg_section": 35,     # square leg cross-section
    "leg_offset": 100,     # leg center offset from the stool axis (X and Y)
    "rail_section": 35,    # footrest rail width (Y)
    "rail_height": 30,     # footrest rail height (Z)
    "rail_bottom": 250,    # footrest underside height
}

# Round seat, top face at stool height.
seat_z = params["stool_height"] - params["seat_thickness"] / 2  # 735
part = Pos(0, 0, seat_z) * Cylinder(
    radius=params["seat_diameter"] / 2, height=params["seat_thickness"])

# Four square legs under the seat.
leg_height = params["stool_height"] - params["seat_thickness"]  # 720
c = params["leg_offset"]  # 100
for sx in (1, -1):
    for sy in (1, -1):
        part += Pos(sx * c, sy * c, leg_height / 2) * Box(
            params["leg_section"], params["leg_section"], leg_height)

result = part
