from build123d import *

params = {"length": 12, "width": 8, "thickness": 0.3, "wall_height": 3.0, "wall_thickness": 0.3}

base = Box(params["length"], params["width"], params["thickness"])

outer_wall = Box(params["length"], params["width"], params["wall_height"])
inner_cut = Box(params["length"] - 2*params["wall_thickness"], params["width"] - 2*params["wall_thickness"], params["wall_height"])
hollow_walls = outer_wall - inner_cut
hollow_walls = Pos(0, 0, 1.65) * hollow_walls

result = base + hollow_walls
