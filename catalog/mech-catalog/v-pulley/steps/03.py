from build123d import *

params = {
    "pulley_radius": 35,
    "pulley_height": 25,
    "groove_width": 12,
    "groove_depth": 10,
    "groove_center_z": 12.5,
    "bore_radius": 10,
}

# Pulley body: a disc along Z.
result = Cylinder(
    radius=params["pulley_radius"],
    height=params["pulley_height"],
    align=(Align.CENTER, Align.CENTER, Align.MIN),
)

# V-groove around the rim: a triangular cross-section revolve cut.
_groove_top = params["groove_center_z"] + params["groove_width"] / 2
_groove_bottom = params["groove_center_z"] - params["groove_width"] / 2
_groove_inner_radius = params["pulley_radius"] - params["groove_depth"]

with BuildPart() as cutter:
    with BuildSketch(Plane.XZ) as profile:
        with BuildLine():
            Polyline(
                (params["pulley_radius"], _groove_bottom),
                (params["pulley_radius"], _groove_top),
                (_groove_inner_radius, params["groove_center_z"]),
                (params["pulley_radius"], _groove_bottom),
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
