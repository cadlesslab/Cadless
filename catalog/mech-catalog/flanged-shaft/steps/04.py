from build123d import *

params = {
    "shaft_radius": 6,
    "shaft_height": 80,
    "flange_radius": 20,
    "flange_height": 8,
    "bolt_circle_radius": 15,
    "bolt_count": 4,
    "bolt_hole_radius": 2.5,
    "chamfer_length": 1,
}

shaft = Cylinder(radius=params["shaft_radius"], height=params["shaft_height"], align=(Align.CENTER, Align.CENTER, Align.MIN))
flange = Cylinder(radius=params["flange_radius"], height=params["flange_height"], align=(Align.CENTER, Align.CENTER, Align.MIN))
part = shaft + flange
for loc in PolarLocations(params["bolt_circle_radius"], params["bolt_count"]):
    part -= loc * Cylinder(radius=params["bolt_hole_radius"], height=params["flange_height"], align=(Align.CENTER, Align.CENTER, Align.MIN))
top = part.edges().filter_by(GeomType.CIRCLE).group_by(Axis.Z)[-1]
result = chamfer(top, length=params["chamfer_length"])
