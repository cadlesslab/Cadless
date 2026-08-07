from build123d import *

params = {
    "journal_radius": 25,  # 50 mm main journals
    "pin_radius": 22.5,  # 45 mm crank pins
    "crank_offset": 43,  # crank radius for an 86 mm stroke
    "web_radius": 70,
    "web_thickness": 18,
    "lobe_radius": 85,  # counterweight lobes, opposite each crank pin
    # Axial layout (nominal faces): J1 0-32, web 32-50, pin1 50-80, web 80-98,
    # J2 98-130, web 130-148, pin2 148-178, web 178-196, J3 196-228,
    # flange 228-240. Journals and pins are extended 2 mm into the webs so
    # every union overlaps instead of merely touching.
    "journal_spans": [(0, 34), (96, 132), (194, 228)],
    "web_centers": [41, 89, 139, 187],
    "pin_spans": [(48, 82), (146, 180)],
    "pin_sides": [1, -1],  # pin 1 at +Z, pin 2 at -Z (180 degree twin)
    "web_pin_side": [1, 1, -1, -1],  # which pin each web serves
    "flange_span": (226, 240),
    "flange_radius": 45,
}

# First main journal along the X axis, starting at X=0.
x0, x1 = params["journal_spans"][0]
part = Pos((x0 + x1) / 2, 0, 0) * Cylinder(
    radius=params["journal_radius"], height=x1 - x0).rotate(Axis.Y, 90)

# First crank throw: web, crank pin offset +43 mm in Z, closing web.
# Built in web -> pin -> web order so the part stays a single connected solid.
p0, p1 = params["pin_spans"][0]
part += Pos(params["web_centers"][0], 0, 0) * Cylinder(
    radius=params["web_radius"], height=params["web_thickness"]).rotate(Axis.Y, 90)
part += Pos((p0 + p1) / 2, 0, params["pin_sides"][0] * params["crank_offset"]) * Cylinder(
    radius=params["pin_radius"], height=p1 - p0).rotate(Axis.Y, 90)
part += Pos(params["web_centers"][1], 0, 0) * Cylinder(
    radius=params["web_radius"], height=params["web_thickness"]).rotate(Axis.Y, 90)

# Centre main journal.
x0, x1 = params["journal_spans"][1]
part += Pos((x0 + x1) / 2, 0, 0) * Cylinder(
    radius=params["journal_radius"], height=x1 - x0).rotate(Axis.Y, 90)

# Second crank throw, pin offset -43 mm in Z (180 degrees from the first).
p0, p1 = params["pin_spans"][1]
part += Pos(params["web_centers"][2], 0, 0) * Cylinder(
    radius=params["web_radius"], height=params["web_thickness"]).rotate(Axis.Y, 90)
part += Pos((p0 + p1) / 2, 0, params["pin_sides"][1] * params["crank_offset"]) * Cylinder(
    radius=params["pin_radius"], height=p1 - p0).rotate(Axis.Y, 90)
part += Pos(params["web_centers"][3], 0, 0) * Cylinder(
    radius=params["web_radius"], height=params["web_thickness"]).rotate(Axis.Y, 90)

# Rear main journal.
x0, x1 = params["journal_spans"][2]
part += Pos((x0 + x1) / 2, 0, 0) * Cylinder(
    radius=params["journal_radius"], height=x1 - x0).rotate(Axis.Y, 90)

# Counterweight lobes: enlarge the half of each web opposite its crank pin.
for wx, pin_side in zip(params["web_centers"], params["web_pin_side"]):
    lobe = Cylinder(radius=params["lobe_radius"],
                    height=params["web_thickness"]).rotate(Axis.Y, 90)
    if pin_side > 0:
        half = Box(params["web_thickness"], 200, 100,
                   align=(Align.CENTER, Align.CENTER, Align.MAX))
    else:
        half = Box(params["web_thickness"], 200, 100,
                   align=(Align.CENTER, Align.CENTER, Align.MIN))
    part += Pos(wx, 0, 0) * (lobe & half)

# Flywheel mounting flange on the rear journal.
f0, f1 = params["flange_span"]
part += Pos((f0 + f1) / 2, 0, 0) * Cylinder(
    radius=params["flange_radius"], height=f1 - f0).rotate(Axis.Y, 90)

result = part
