from build123d import *

params = {
    "journal_radius": 25,  # 50 mm main journals
    # Axial layout (nominal faces): J1 0-32, web 32-50, pin1 50-80, web 80-98,
    # J2 98-130, web 130-148, pin2 148-178, web 178-196, J3 196-228,
    # flange 228-240. Journals and pins are extended 2 mm into the webs so
    # every union overlaps instead of merely touching.
    "journal_spans": [(0, 34)],
}

# First main journal along the X axis, starting at X=0.
x0, x1 = params["journal_spans"][0]
part = Pos((x0 + x1) / 2, 0, 0) * Cylinder(
    radius=params["journal_radius"], height=x1 - x0).rotate(Axis.Y, 90)

result = part
