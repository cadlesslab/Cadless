from build123d import *

params = {
    "block_width": 60,
    "block_depth": 40,
    "block_height": 40,
    "v_cutter_side": 28.2843,  # 40 mm diagonal -> 16 mm deep, 32 mm wide V
    "v_center_z": 44,
}

# Block blank centered in X/Y, resting on Z=0.
part = Box(
    params["block_width"],
    params["block_depth"],
    params["block_height"],
    align=(Align.CENTER, Align.CENTER, Align.MIN),
)

# 90-degree V-groove along Y: a 45-degree rotated square prism cutter.
cutter = Box(
    params["v_cutter_side"],
    params["block_depth"] + 4,
    params["v_cutter_side"],
).rotate(Axis.Y, 45)
part -= Pos(0, 0, params["v_center_z"]) * cutter

result = part
