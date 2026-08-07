from build123d import *

params = {
    "base_length": 80,
    "base_width": 60,
    "base_height": 8,
    "leg_length": 80,
    "leg_width": 8,
    "leg_height": 50,
    "fillet_radius": 6,
    "hole_radius": 3,
    "base_hole_1_x": 20,
    "base_hole_1_y": 40,
    "base_hole_2_x": 60,
    "base_hole_2_y": 40,
    "leg_hole_1_x": 20,
    "leg_hole_1_z": 30,
    "leg_hole_2_x": 60,
    "leg_hole_2_z": 30,
    "edge_filter_tolerance": 0.6,
}

base = Box(params["base_length"], params["base_width"], params["base_height"], align=(Align.MIN, Align.MIN, Align.MIN))
leg = Box(params["leg_length"], params["leg_width"], params["leg_height"], align=(Align.MIN, Align.MIN, Align.MIN))
part = base + leg

# Inner concave fillet where the two plates meet.
inner = [
    e
    for e in part.edges().filter_by(Axis.X)
    if abs(e.center().Y - params["leg_width"]) < params["edge_filter_tolerance"] and abs(e.center().Z - params["base_height"]) < params["edge_filter_tolerance"]
]
part = fillet(inner, radius=params["fillet_radius"])

# Two 6 mm holes through the base plate (along Z, full 8 mm thickness).
part -= Pos(params["base_hole_1_x"], params["base_hole_1_y"], 0) * Cylinder(radius=params["hole_radius"], height=params["base_height"], align=(Align.CENTER, Align.CENTER, Align.MIN))
part -= Pos(params["base_hole_2_x"], params["base_hole_2_y"], 0) * Cylinder(radius=params["hole_radius"], height=params["base_height"], align=(Align.CENTER, Align.CENTER, Align.MIN))

# Two 6 mm holes through the vertical leg (along Y, full 8 mm thickness).
leg_hole = Cylinder(radius=params["hole_radius"], height=params["leg_width"], align=(Align.CENTER, Align.CENTER, Align.MIN)).rotate(Axis.X, -90)
part -= Pos(params["leg_hole_1_x"], 0, params["leg_hole_1_z"]) * leg_hole
part -= Pos(params["leg_hole_2_x"], 0, params["leg_hole_2_z"]) * leg_hole

result = part
