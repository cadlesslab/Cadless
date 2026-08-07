from build123d import *

params = {
    "box_length": 80,
    "box_width": 60,
    "box_height": 30,
    "wall_thickness": 3,
}

# Outer enclosure box, hollowed to a wall thickness, open at the top (+Z) face.
with BuildPart() as p:
    Box(params["box_length"], params["box_width"], params["box_height"])
    offset(amount=-params["wall_thickness"], openings=p.faces().sort_by(Axis.Z)[-1])

result = p.part
