from build123d import *

params = {
    "length": 300,  # along the crank axis (X)
    "width": 160,
    "height": 220,  # deck face at Z=220
}

# Block blank: base on the XY plane, deck face on top at Z=220.
part = Box(
    params["length"], params["width"], params["height"],
    align=(Align.CENTER, Align.CENTER, Align.MIN),
)

result = part
