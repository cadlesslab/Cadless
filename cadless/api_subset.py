"""The constrained API subset the LLM may target, and the allow/deny policy the
static validator (see ``cadless.validation``) enforces.

This module is pure data + helpers — no build123d import — so it is cheap to
import from the prompt builder, the validator, and tests alike.

Policy shape (PoC level, see ADR-002 §4):
  * **Import allow-list** — only these top-level modules may be imported.
  * **Name deny-list** — escape-hatch builtins that are never allowed, even
    though most builtins are permitted.
  * **Dunder attribute block** — access to ``__...__`` attributes is rejected
    (blocks ``().__class__.__bases__`` style sandbox escapes).
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

# The variable the generated script must assign its model to.
RESULT_VARIABLE = "result"

# What ``result`` may be, as the system prompt states it and as the edit and
# repair rounds of a turn that did not ask for an assembly close on it: one
# place, so they cannot ask for different things.
RESULT_SHAPE = (
    "one solid, or a `Compound(children=[...])` of the separate parts, each placed "
    "where it sits assembled and given a label"
)

# Curated description of the allowed build123d surface, injected into the system
# prompt so the model targets exactly what the validator permits.
API_SUBSET_DOC = """\
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
  * Only `from build123d import *`, `import math`, and `import copy` are allowed.
  * Assign the model (a Part/Solid/Compound) to a variable named `result`.
  * No file, network, OS, or process access of any kind.
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
    """True if `module_name` (the top-level package) may be imported."""
    top = module_name.split(".", 1)[0]
    return top in ALLOWED_IMPORT_MODULES


def describe_policy() -> dict[str, object]:
    """Machine-readable snapshot of the policy (used by docs/tests/tooling)."""
    return {
        "allowed_import_modules": sorted(ALLOWED_IMPORT_MODULES),
        "banned_import_modules": sorted(BANNED_IMPORT_MODULES),
        "banned_names": sorted(BANNED_NAMES),
        "result_variable": RESULT_VARIABLE,
    }
