from build123d import *

params = {
    "panel_length": 100,
    "panel_width": 70,
    "panel_thickness": 3,
    "corner_radius": 5,
    "button_radius": 11.15,  # 22.3 mm cutout for 22 mm pushbuttons
    "button_dx": 28,
    "button_y": -15,
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

# Two 22 mm pushbutton cutouts.
for side in (1, -1):
    hole = Cylinder(
        radius=params["button_radius"],
        height=params["panel_thickness"] + 2,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    )
    part -= Pos(side * params["button_dx"], params["button_y"], -1) * hole

result = part
