from build123d import *

params = {
    "seat_length": 1200,     # seat length (X), mm
    "seat_depth": 350,       # seat depth (Y), mm
    "seat_thickness": 40,
    "bench_height": 450,     # floor to seat surface
    "leg_thickness": 40,     # panel leg thickness (X)
    "leg_inset": 100,        # leg outer face inset from the seat ends
    "stretcher_width": 30,   # stretcher thickness (Y)
    "stretcher_height": 80,
    "stretcher_bottom": 200, # stretcher underside height
}

# Seat slab, top face at bench height.
seat_z = params["bench_height"] - params["seat_thickness"] / 2  # 430
part = Pos(0, 0, seat_z) * Box(
    params["seat_length"], params["seat_depth"], params["seat_thickness"])

# Two full-depth panel legs.
leg_height = params["bench_height"] - params["seat_thickness"]  # 410
leg_x = params["seat_length"] / 2 - params["leg_inset"] - params["leg_thickness"] / 2  # 480
for sx in (1, -1):
    part += Pos(sx * leg_x, 0, leg_height / 2) * Box(
        params["leg_thickness"], params["seat_depth"], leg_height)

result = part
