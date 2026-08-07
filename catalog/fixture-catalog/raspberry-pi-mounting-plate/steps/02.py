from build123d import *

params = {
    "plate_length": 92,
    "plate_width": 61,
    "plate_thickness": 4,
    "corner_radius": 6,
}

# Base plate centered in X/Y, resting on Z=0.
part = Box(
    params["plate_length"],
    params["plate_width"],
    params["plate_thickness"],
    align=(Align.CENTER, Align.CENTER, Align.MIN),
)

# Round the plate corners.
corner_edges = part.edges().filter_by(Axis.Z)
part = fillet(corner_edges, radius=params["corner_radius"])

result = part
