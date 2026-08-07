from build123d import *

params = {
    "top_length": 1200,        # desktop length (X), mm
    "top_depth": 600,          # desktop depth (Y), mm
    "top_thickness": 25,
    "desk_height": 740,        # floor to top surface
    "panel_thickness": 18,     # side panel thickness
    "modesty_thickness": 16,
    "modesty_bottom": 300,     # modesty panel lower edge height
    "grommet_diameter": 60,
    "grommet_from_back": 100,  # grommet center from the back edge
}

# Desktop slab, top face at desk height. Back edge is at +Y.
top_z = params["desk_height"] - params["top_thickness"] / 2  # 727.5
part = Pos(0, 0, top_z) * Box(
    params["top_length"], params["top_depth"], params["top_thickness"])

# Two full-depth side panels, outer faces flush with the desktop ends.
panel_height = params["desk_height"] - params["top_thickness"]  # 715
panel_x = params["top_length"] / 2 - params["panel_thickness"] / 2  # 591
for sx in (1, -1):
    part += Pos(sx * panel_x, 0, panel_height / 2) * Box(
        params["panel_thickness"], params["top_depth"], panel_height)

# Modesty panel spanning between the side panels at the back.
modesty_length = params["top_length"] - 2 * params["panel_thickness"]  # 1164
modesty_y = params["top_depth"] / 2 - params["modesty_thickness"] / 2  # 292, flush with the back
modesty_height = panel_height - params["modesty_bottom"]  # 415
part += Pos(0, modesty_y, params["modesty_bottom"] + modesty_height / 2) * Box(
    modesty_length, params["modesty_thickness"], modesty_height)

# Cable grommet through the desktop near the back edge.
grommet_y = params["top_depth"] / 2 - params["grommet_from_back"]  # 200
part -= Pos(0, grommet_y, top_z) * Cylinder(
    radius=params["grommet_diameter"] / 2, height=params["top_thickness"] + 10)

result = part
