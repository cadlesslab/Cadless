from build123d import *

params = {
    "block_width": 60,
    "block_depth": 40,
    "block_height": 40,
    "v_cutter_side": 28.2843,  # 40 mm diagonal -> 16 mm deep, 32 mm wide V
    "v_center_z": 44,
    "side_groove_height": 8,
    "side_groove_depth": 3,
    "side_groove_z": 20,  # groove centreline height
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

# Clamp grooves in the side faces.
for side in (1, -1):
    groove = Box(
        params["side_groove_depth"] * 2,
        params["block_depth"] + 4,
        params["side_groove_height"],
    )
    part -= Pos(side * params["block_width"] / 2, 0, params["side_groove_z"]) * groove

result = part
