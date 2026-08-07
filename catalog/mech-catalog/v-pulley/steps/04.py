from build123d import *

params = {
    "pulley_radius": 35,
    "pulley_height": 25,
    "groove_width": 12,
    "groove_depth": 10,
    "groove_center_z": 12.5,
    "bore_radius": 10,
    "set_screw_radius": 2.5,
    "set_screw_length": 40,
    "set_screw_z": 12.5,
}

# Pulley body: a disc along Z.
result = Cylinder(
    radius=params["pulley_radius"],
    height=params["pulley_height"],
    align=(Align.CENTER, Align.CENTER, Align.MIN),
)

# V-groove around the rim: a triangular cross-section revolve cut.
groove_top_z = params["groove_center_z"] + params["groove_width"] / 2
groove_bottom_z = params["groove_center_z"] - params["groove_width"] / 2
groove_inner_radius = params["pulley_radius"] - params["groove_depth"]

with BuildPart() as cutter:
    with BuildSketch(Plane.XZ) as profile:
        with BuildLine():
            Polyline(
                (params["pulley_radius"], groove_bottom_z),
                (params["pulley_radius"], groove_top_z),
                (groove_inner_radius, params["groove_center_z"]),
                (params["pulley_radius"], groove_bottom_z),
            )
        make_face()
    revolve(axis=Axis.Z)

result = result - cutter.part

# Central bore: a hole through the centre.
result = result - Cylinder(
    radius=params["bore_radius"],
    height=params["pulley_height"],
    align=(Align.CENTER, Align.CENTER, Align.MIN),
)

# Set-screw hole: a radial hole through the hub into the bore.
set_screw = (
    Cylinder(
        radius=params["set_screw_radius"],
        height=params["set_screw_length"],
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    )
    .rotate(Axis.Y, 90)
    .translate((0, 0, params["set_screw_z"]))
)
result = result - set_screw
