from build123d import *

params = {
    "panel_length": 100,
    "panel_width": 70,
    "panel_thickness": 3,
    "corner_radius": 5,
}

# Panel plate centered in X/Y, resting on Z=0.
part = Box(
    params["panel_length"],
    params["panel_width"],
    params["panel_thickness"],
    align=(Align.CENTER, Align.CENTER, Align.MIN),
)

# Round the panel corners.
corner_edges = part.edges().filter_by(Axis.Z)
part = fillet(corner_edges, radius=params["corner_radius"])

result = part
