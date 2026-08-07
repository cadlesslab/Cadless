from build123d import *

params = {
    "base_length": 40,
    "base_width": 16,
    "base_thickness": 5,
    "bridge_length": 20,
    "bridge_height": 16,
    "bore_radius": 5,  # 10 mm cable
    "bore_height": 9,
    "screw_hole_radius": 2.2,
    "screw_hole_x": 15,
    "chamfer_length": 1.5,
}

# Clamp base bar centered in X/Y, resting on Z=0.
part = Box(
    params["base_length"],
    params["base_width"],
    params["base_thickness"],
    align=(Align.CENTER, Align.CENTER, Align.MIN),
)

# Saddle bridge over the centre of the base.
bridge = Box(
    params["bridge_length"],
    params["base_width"],
    params["bridge_height"],
    align=(Align.CENTER, Align.CENTER, Align.MIN),
)
part += bridge

# Cable bore through the bridge along Y.
bore = Cylinder(
    radius=params["bore_radius"],
    height=params["base_width"] + 4,
).rotate(Axis.X, 90)
part -= Pos(0, 0, params["bore_height"]) * bore

# Two hold-down screw holes through the base tabs.
for side in (1, -1):
    hole = Cylinder(
        radius=params["screw_hole_radius"],
        height=params["base_thickness"] + 2,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    )
    part -= Pos(side * params["screw_hole_x"], 0, -1) * hole

# Break the bridge's top edges.
top_edges = part.edges().filter_by(GeomType.LINE).group_by(Axis.Z)[-1]
part = chamfer(top_edges, length=params["chamfer_length"])

result = part
