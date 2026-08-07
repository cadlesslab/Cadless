from build123d import *

params = {
    "pulley_radius": 35,
    "pulley_height": 25,
    "groove_top_z": 18.5,
    "groove_bottom_z": 6.5,
    "groove_center_z": 12.5,
    "groove_inner_radius": 25,
}

result = Cylinder(
    radius=params["pulley_radius"],
    height=params["pulley_height"],
    align=(Align.CENTER, Align.CENTER, Align.MIN),
)

with BuildPart() as cutter:
    with BuildSketch(Plane.XZ) as profile:
        with BuildLine():
            Polyline(
                (params["pulley_radius"], params["groove_bottom_z"]),
                (params["pulley_radius"], params["groove_top_z"]),
                (params["groove_inner_radius"], params["groove_center_z"]),
                (params["pulley_radius"], params["groove_bottom_z"]),
            )
        make_face()
    revolve(axis=Axis.Z)

result = result - cutter.part
