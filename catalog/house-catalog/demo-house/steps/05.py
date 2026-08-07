from build123d import *

params = {"length": 12, "width": 8, "thickness": 0.3, "wall_height": 3.0, "wall_thickness": 0.3, "door_width": 1.0, "door_depth": 1.0, "door_height": 2.1, "roof_length": 12.4, "roof_width": 8.4, "roof_z": 6.6}

base = Box(params["length"], params["width"], params["thickness"])

outer_wall = Box(params["length"], params["width"], params["wall_height"])
inner_cut = Box(params["length"] - 2*params["wall_thickness"], params["width"] - 2*params["wall_thickness"], params["wall_height"])
hollow_walls = outer_wall - inner_cut
hollow_walls = Pos(0, 0, 1.65) * hollow_walls

door_cut = Pos(0, -4.0, 1.2) * Box(params["door_width"], params["door_depth"], params["door_height"])

second_floor_slab = Pos(0, 0, 3.3) * Box(params["length"], params["width"], params["thickness"])

second_floor_outer_wall = Box(params["length"], params["width"], params["wall_height"])
second_floor_inner_cut = Box(params["length"] - 2*params["wall_thickness"], params["width"] - 2*params["wall_thickness"], params["wall_height"])
second_floor_hollow_walls = second_floor_outer_wall - second_floor_inner_cut
second_floor_hollow_walls = Pos(0, 0, 4.95) * second_floor_hollow_walls

roof_slab = Pos(0, 0, params["roof_z"]) * Box(params["roof_length"], params["roof_width"], params["thickness"])

result = base + hollow_walls - door_cut + second_floor_slab + second_floor_hollow_walls + roof_slab
