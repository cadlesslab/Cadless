from build123d import *

params = {"across_flats": 20, "height": 25, "bore_dia": 8}
profile = RegularPolygon(radius=params["across_flats"] / 2, side_count=6, major_radius=False)
body = extrude(profile, amount=params["height"])
bore = Cylinder(radius=params["bore_dia"] / 2, height=params["height"] * 2)
result = body - bore
