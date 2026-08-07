from build123d import *

params = {
    "leg_length": 60,
    "leg_width": 40,
    "leg_thickness": 5,
    "gusset_size": 25,
    "gusset_thickness": 6,
    "hole_radius": 2.75,  # M5 clearance, 5.5 mm diameter
    "hole_offsets": [35, 50],
}

# Horizontal leg from X=0 to X=60, centered in Y, resting on Z=0.
part = Box(
    params["leg_length"],
    params["leg_width"],
    params["leg_thickness"],
    align=(Align.MIN, Align.CENTER, Align.MIN),
)

# Vertical leg sharing the corner root.
upright = Box(
    params["leg_thickness"],
    params["leg_width"],
    params["leg_length"],
    align=(Align.MIN, Align.CENTER, Align.MIN),
)
part += upright

# Triangular gusset rib between the legs, centered on Y=0.
root = params["leg_thickness"]
profile = Polyline(
    (root, root),
    (root + params["gusset_size"], root),
    (root, root + params["gusset_size"]),
    close=True,
)
face = make_face(Plane.XZ * profile)
gusset = extrude(face, amount=params["gusset_thickness"] / 2, both=True)
part += gusset

# Two M5 clearance holes per leg, on the centreline.
for d in params["hole_offsets"]:
    down_hole = Cylinder(
        radius=params["hole_radius"],
        height=params["leg_thickness"] + 2,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    )
    part -= Pos(d, 0, -1) * down_hole
    side_hole = Cylinder(
        radius=params["hole_radius"],
        height=params["leg_thickness"] + 2,
    ).rotate(Axis.Y, 90)
    part -= Pos(params["leg_thickness"] / 2, 0, d) * side_hole

result = part
