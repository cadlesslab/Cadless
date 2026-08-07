from build123d import *

params = {
    "leg_length": 60,
    "leg_width": 40,
    "leg_thickness": 5,
    "gusset_size": 25,
    "gusset_thickness": 6,
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

result = part
