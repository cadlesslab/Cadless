from build123d import *

params = {
    "base_length": 40,
    "base_width": 16,
    "base_thickness": 5,
    "bridge_length": 20,
    "bridge_height": 16,
    "bore_radius": 5,  # 10 mm cable
    "bore_height": 9,
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

result = part
