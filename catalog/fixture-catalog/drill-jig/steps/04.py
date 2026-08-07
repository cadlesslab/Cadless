from build123d import *

params = {
    "body_length": 70,
    "body_width": 40,
    "body_thickness": 15,
    "fence_width": 8,
    "fence_height": 12,
    "fence_drop": 8,  # how far the fence hangs below the base
    "guide_radius": 3,  # 6 mm drill guide bore
    "guide_spacing": 20,
    "cbore_radius": 5,
    "cbore_depth": 5,
}

# Jig body centered in X/Y, resting on Z=0.
part = Box(
    params["body_length"],
    params["body_width"],
    params["body_thickness"],
    align=(Align.CENTER, Align.CENTER, Align.MIN),
)

# Registration fence hooked over the workpiece edge.
fence = Box(
    params["body_length"],
    params["fence_width"],
    params["fence_height"],
    align=(Align.CENTER, Align.MAX, Align.MIN),
)
part += Pos(0, -params["body_width"] / 2, -params["fence_drop"]) * fence

# Three drill guide bores through the body.
for x in (-params["guide_spacing"], 0, params["guide_spacing"]):
    bore = Cylinder(
        radius=params["guide_radius"],
        height=params["body_thickness"] + 2,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    )
    part -= Pos(x, 0, -1) * bore

# Bushing counterbores from the top face.
for x in (-params["guide_spacing"], 0, params["guide_spacing"]):
    cbore = Cylinder(
        radius=params["cbore_radius"],
        height=params["cbore_depth"] + 1,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    )
    part -= Pos(x, 0, params["body_thickness"] - params["cbore_depth"]) * cbore

result = part
