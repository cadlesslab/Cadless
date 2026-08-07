from build123d import *

params = {
    "base_length": 80,
    "base_width": 60,
    "base_height": 8,
    "leg_length": 80,
    "leg_width": 8,
    "leg_height": 50,
    "fillet_radius": 6,
}

base = Box(
    params["base_length"],
    params["base_width"],
    params["base_height"],
    align=(Align.MIN, Align.MIN, Align.MIN),
)
leg = Box(
    params["leg_length"],
    params["leg_width"],
    params["leg_height"],
    align=(Align.MIN, Align.MIN, Align.MIN),
)
part = base + leg

# Inner concave edge where the two plates meet: runs along X at y~leg_width (inner
# face of the leg) and z~base_height (top face of the base). Pick it by edge center.
inner = [
    e
    for e in part.edges().filter_by(Axis.X)
    if abs(e.center().Y - params["leg_width"]) < 0.6
    and abs(e.center().Z - params["base_height"]) < 0.6
]
result = fillet(inner, radius=params["fillet_radius"])
