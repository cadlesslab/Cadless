from build123d import *

params = {
    "box_length": 80,
    "box_width": 60,
    "box_height": 30,
}

# Outer enclosure box: 80 (X) x 60 (Y) x 30 (Z) mm, centered at the origin.
result = Box(params["box_length"], params["box_width"], params["box_height"])
