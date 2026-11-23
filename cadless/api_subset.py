"""The constrained API subset the LLM may target, and the allow/deny policy the
static validator (see ``cadless.validation``) enforces.

This module is pure data + helpers — no build123d import — so it is cheap to
import from the prompt builder, the validator, and tests alike.

Policy shape (see ADR-0003):
  * **Import allow-list** — only these modules may be imported, by their exact
    name: a submodule is a module of its own, holding whatever it imported. A
    name taken from build123d or copy is one of the names reviewed for scripts.
  * **Name deny-list** — escape-hatch builtins, dunder names and withheld
    build123d names are refused wherever a script writes a name, and so is a
    builtin outside the allow-list that the script does not define itself.
  * **Attribute block** — dunder and private (``_x``) attributes, the
    attributes of frames, generators, coroutines, tracebacks and code objects,
    and the few methods that look attributes up from text or hand out what the
    rest of the policy keeps back; and no attribute set or deleted but the few
    that place, name and colour a part.
  * **Statement block** — no class definitions and no ``match`` statements.
  * **Builtins allow-list** — the builtins a script runs with.
  * **Result convention** — the script must bind its model to ``result``.
"""

from __future__ import annotations

# Modules the generated script is permitted to import.
ALLOWED_IMPORT_MODULES: frozenset[str] = frozenset(
    {
        "build123d",
        "math",
        "copy",
    }
)

# Modules that are common sandbox-escape / side-effect vectors. Listed
# explicitly for clear error messages; anything not in ALLOWED_IMPORT_MODULES
# is rejected regardless, but naming these gives the user a precise reason.
BANNED_IMPORT_MODULES: frozenset[str] = frozenset(
    {
        "os",
        "sys",
        "subprocess",
        "socket",
        "shutil",
        "pathlib",
        "importlib",
        "ctypes",
        "threading",
        "multiprocessing",
        "asyncio",
        "requests",
        "urllib",
        "http",
        "pickle",
        "marshal",
        "builtins",
        "gc",
        "inspect",
        "io",
    }
)

# Builtin names that are never callable from generated code.
BANNED_NAMES: frozenset[str] = frozenset(
    {
        "eval",
        "exec",
        "compile",
        "open",
        "__import__",
        "input",
        "exit",
        "quit",
        "globals",
        "locals",
        "vars",
        "getattr",
        "setattr",
        "delattr",
        "breakpoint",
        "help",
        "memoryview",
    }
)

# Attribute-name prefixes of the interpreter's own objects: frames (f_),
# generators (gi_), coroutines (cr_), async generators (ag_), tracebacks (tb_)
# and code objects (co_). A frame hands out the globals and builtins of the code
# it ran, which no namespace given to a script restricts, so the gate refuses
# them by name.
INTERNALS_ATTRIBUTE_PREFIXES: tuple[str, ...] = ("f_", "gi_", "cr_", "ag_", "tb_", "co_")

# Methods that look attributes up from text the gate cannot read:
# ``"{0.x}".format(obj)`` reads ``obj.x`` with no attribute in the code.
STRING_LOOKUP_ATTRIBUTES: frozenset[str] = frozenset({"format", "format_map"})

# Attributes that hand a script what the rest of the policy keeps from it:
# ``mro`` reaches ``object`` and ``BaseException`` from any class, and
# ``copy_attributes_to`` copies an object's attributes, private ones included,
# onto another object.
REFUSED_ATTRIBUTES: frozenset[str] = frozenset({"mro", "copy_attributes_to"})

# The only attributes a script may set: a part's name and colour, where it sits,
# and which assembly it belongs to -- each a real change to the object it is set
# on. Everything that measures a build reads it through methods and attributes
# of the objects the build made, so a script that could set any other attribute
# -- a method on a shape, an attribute of a function of its own -- could have the
# build report what it chose. Setting one of these on a class rather than an
# object is caught where the script runs. Left out on purpose, though build123d
# has setters for them: a vector's components and a plane's origin and axes,
# which a script can as well build anew.
WRITABLE_ATTRIBUTES: frozenset[str] = frozenset(
    {"label", "color", "location", "position", "orientation", "parent", "children"}
)

# build123d names a script is never handed, as a name or as an attribute: its
# file and network functions, and its font manager, which scans and opens font
# files wherever it is pointed.
BUILD123D_WITHHELD: frozenset[str] = frozenset(
    {
        "Export2D",
        "ExportDXF",
        "ExportSVG",
        "FontManager",
        "Mesher",
        "available_fonts",
        "export_brep",
        "export_gltf",
        "export_step",
        "export_stl",
        "export_to_pcbway",
        "import_brep",
        "import_dxf",
        "import_step",
        "import_stl",
        "import_svg",
        "import_svg_as_buildline_code",
    }
)

# build123d's public names as reviewed for what a script may be handed. A script
# gets these, less the withheld ones, and never a name a later build123d adds:
# a new name -- a file function among them -- is reviewed and added here first.
BUILD123D_REVIEWED: frozenset[str] = frozenset(
    """
    add Airfoil Align AngularDirection ApproxOption ArcArcTangentArc ArcArcTangentLine
    Arrow ArrowHead available_fonts Axis BallJoint BaseLineObject BasePartObject
    BaseSketchObject Bezier BlendCurve BoundBox bounding_box Box BSpline BuildLine
    BuildPart BuildSketch CenterArc CenterOf chamfer Circle CM Color Compound Cone
    ConstrainedArcs ConstrainedLines ContinuityLevel ConvexPolyhedron CounterBoreHole
    CounterSinkHole Curve Cylinder CylindricalJoint delta detect_primitives
    DimensionLine DotLength DoubleTangentArc Draft draft DraftAngleError Edge edge edges
    edges_to_wires Ellipse EllipticalCenterArc EllipticalStartArc Export2D export_brep
    export_gltf export_step export_stl export_to_pcbway ExportDXF ExportSVG
    ExtensionLine Extrinsic extrude Face face faces fillet FilletPolyline FontManager
    FontStyle FrameMethod FT full_round G GeomEncoder GeomType GridLocations HeadType
    Helix HexLocations Hole HyperbolicCenterArc import_brep import_dxf import_step
    import_stl import_svg import_svg_as_buildline_code IN IntersectingLine Intrinsic
    JernArc Joint Keep KG Kind LB LengthMode Line LinearJoint LineType Location
    LocationEncoder Locations loft M make_brake_formed make_face make_hull Matrix MC
    Mesher MeshType mirror MM Mode new_edges NumberDisplay offset OrientedBoundBox pack
    PageSize ParabolicCenterArc Part Plane PointArcTangentArc PointArcTangentLine polar
    PolarLine PolarLocations Polygon Polyline Pos PositionMode PrecisionMode project
    project_workplane RadiusArc Rectangle RectangleRounded RegularPolygon RevoluteJoint
    revolve RigidJoint Rot Rotation RotationLike Sagitta SagittaArc scale section Select
    ShapeList Shell Side Sketch SlotArc SlotCenterPoint SlotCenterToCenter SlotOverall
    Solid solid solids SortBy Sphere Spline split sweep Tangency TangentArc
    TechnicalDrawing Text TextAlign thicken THOU ThreePointArc topo_distance_to
    topo_explore_common_vertex topo_explore_connected_edges Torus trace Transition
    Trapezoid Triangle Unit UNITS_PER_METER Until Vector VectorLike Vertex vertex
    vertices Wedge Wire wire wires
    """.split()
)

# What a script gets from ``copy``: its two functions and their error, and
# nothing else the module holds.
COPY_NAMES: frozenset[str] = frozenset({"Error", "copy", "deepcopy"})

# The builtins a script runs with: what a build uses, and nothing that reaches
# past the rest of the policy. Left out on purpose: the real ``__import__``,
# ``type``, ``object``, ``super`` and the descriptors (any class of anything, or
# a new one), the string-driven ``getattr`` family and ``vars``/``dir``, and
# every exception outside ``Exception`` -- ``SystemExit`` and its kind end the
# process before it reports.
SCRIPT_BUILTINS: frozenset[str] = frozenset(
    {
        "abs",
        "all",
        "any",
        "bin",
        "bool",
        "callable",
        "chr",
        "complex",
        "dict",
        "divmod",
        "enumerate",
        "filter",
        "float",
        "format",
        "frozenset",
        "hex",
        "int",
        "isinstance",
        "issubclass",
        "iter",
        "len",
        "list",
        "map",
        "max",
        "min",
        "next",
        "oct",
        "ord",
        "pow",
        "print",
        "range",
        "repr",
        "reversed",
        "round",
        "set",
        "slice",
        "sorted",
        "str",
        "sum",
        "tuple",
        "zip",
        "Ellipsis",
        "NotImplemented",
        "ArithmeticError",
        "AssertionError",
        "AttributeError",
        "Exception",
        "IndexError",
        "KeyError",
        "LookupError",
        "NotImplementedError",
        "OverflowError",
        "RuntimeError",
        "StopIteration",
        "TypeError",
        "ValueError",
        "ZeroDivisionError",
    }
)

# The variable the generated script must assign its model to.
RESULT_VARIABLE = "result"

# What ``result`` may be, as the system prompt states it and as the edit and
# repair rounds of a turn that did not ask for an assembly close on it: one
# place, so they cannot ask for different things.
RESULT_SHAPE = (
    "one solid, or a `Compound(children=[...])` of the separate parts, each placed "
    "where it sits assembled and given a label"
)

# The builtins named to the model, from the allow-list itself so the two cannot
# drift: functions and types first, then the constants and exceptions.
_BUILTINS_LISTED = ", ".join(
    sorted(SCRIPT_BUILTINS, key=lambda name: (name[0].isupper(), name.lower()))
)

# Curated description of the allowed build123d surface, injected into the system
# prompt so the model targets exactly what the validator permits.
API_SUBSET_DOC = f"""\
Allowed building blocks (build123d, imported via `from build123d import *`):

Primitives (Algebra mode):  Box, Cylinder, Sphere, Cone, Torus, Wedge
2D + extrude:               Rectangle, Circle, RegularPolygon, Polygon, Text,
                            extrude(), revolve(), loft(), sweep()
Booleans:                   + (fuse), - (cut), & (intersect)
Modifiers:                  fillet(), chamfer(), mirror(), offset(), scale()
Placement:                  Pos(x, y, z), Rot(x, y, z), Plane, Location, and the
                            `*` operator to place objects (e.g. Pos(0,0,5) * Box(...))
Selectors:                  .edges(), .faces(), .vertices(), .wires(), and group
                            selectors like .edges().group_by(Axis.Z)[-1]
Helpers:                    Axis, Align, Mode, Vector, math module

Rules the generated code MUST follow:
  * Only `from build123d import *`, `import math`, and `import copy` are allowed
    -- never a submodule of build123d.
  * Assign the model (a Part/Solid/Compound) to a variable named `result`.
  * No file, network, OS, or process access of any kind: build123d's import_*,
    export_* and font functions are not available.
  * No class definitions and no `match` statements; write functions instead.
  * No `__dunder__` names, and no attribute that starts with `_`.
  * The only attributes a script sets are `.label`, `.color`, `.location`,
    `.position`, `.orientation`, `.parent` and `.children`, on objects it made.
  * Build text with f-strings: `.format()` is not available.
  * The only builtins are {_BUILTINS_LISTED}; there is no type(), getattr() or
    hasattr().
  * Use millimetres as the unit.

Assemblies: a model of several parts is a Compound of separate solids.
  * Build each part on its own, in its own frame, then place it where it sits in
    the finished product with Pos(...) * Rot(...) * part.
  * Give every part a name: part.label = "wheel_fl". Give a sub-assembly its own
    Compound(children=[...], label="front_axle"); nesting is allowed.
  * Leave clearance at every joint so that no two parts share any volume (the
    engine measures this), and never fuse two parts that must move or be printed
    apart -- a part printed in place, like a link of a chain, is still its own
    part.
  * result = Compound(children=[...]) of the parts. A one-part model is just that
    part.
"""

# A minimal example that PASSES the policy.
ALLOWED_EXAMPLE = """\
from build123d import *

length, width, height = 40, 20, 10
result = Box(length, width, height)
result = fillet(result.edges().filter_by(Axis.Z), radius=2)
"""

# An example that is REJECTED (banned import + file write).
REJECTED_EXAMPLE = """\
import os
from build123d import *

result = Box(10, 10, 10)
os.system("rm -rf /")
open("/tmp/leak", "w").write("nope")
"""


def is_module_allowed(module_name: str) -> bool:
    """True if `module_name`, exactly as written, may be imported.

    Never a submodule of an allowed package: a submodule holds whatever it
    imported, so allowing one by its package's name would allow those too.
    """
    return module_name in ALLOWED_IMPORT_MODULES


def describe_policy() -> dict[str, object]:
    """Machine-readable snapshot of the policy (used by docs/tests/tooling)."""
    return {
        "allowed_import_modules": sorted(ALLOWED_IMPORT_MODULES),
        "banned_import_modules": sorted(BANNED_IMPORT_MODULES),
        "banned_names": sorted(BANNED_NAMES),
        "internals_attribute_prefixes": list(INTERNALS_ATTRIBUTE_PREFIXES),
        "string_lookup_attributes": sorted(STRING_LOOKUP_ATTRIBUTES),
        "refused_attributes": sorted(REFUSED_ATTRIBUTES),
        "writable_attributes": sorted(WRITABLE_ATTRIBUTES),
        "build123d_withheld": sorted(BUILD123D_WITHHELD),
        "build123d_reviewed": sorted(BUILD123D_REVIEWED),
        "copy_names": sorted(COPY_NAMES),
        "script_builtins": sorted(SCRIPT_BUILTINS),
        "result_variable": RESULT_VARIABLE,
    }
