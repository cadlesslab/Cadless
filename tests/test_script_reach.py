"""What build123d puts within a script's reach, held to what was reviewed.

A script is handed build123d's reviewed public names, so what those names lead
to is as much a part of the policy as the gate's rules: a later release can add
a file function, or a class attribute that holds a module, without anything in
this repository changing. Marked build123d.
"""

from __future__ import annotations

import ast
import builtins
import io
import re
import sys
import types
from pathlib import Path

import build123d
import pytest
from build123d import (
    Box,
    BuildLine,
    BuildPart,
    BuildSketch,
    Circle,
    Compound,
    Line,
    Location,
    Plane,
    Pos,
    Text,
    Vector,
    extrude,
)

import cadless
from cadless.api_subset import BUILD123D_REVIEWED, BUILD123D_WITHHELD
from cadless.script_runtime import script_globals
from cadless.validation import attribute_refusal

pytestmark = pytest.mark.build123d

# How build123d names what reads or writes files.
_FILE_NAME = re.compile(r"^(import_|export_|Export|Import)")

# The functions in build123d's own source that read or write an attribute by a
# computed name, each reviewed: a fixed set of names, a copy of an object's own
# attributes, or a name withheld or refused. A new one could hand a script an
# attribute the gate never sees named.
_REVIEWED_COMPUTED_ACCESS = frozenset(
    {
        "build123d/geometry.py:OrientedBoundBox.center",
        "build123d/geometry.py:OrientedBoundBox.x_direction",
        "build123d/geometry.py:OrientedBoundBox.y_direction",
        "build123d/geometry.py:OrientedBoundBox.z_direction",
        "build123d/importers.py:import_svg_as_buildline_code",
        "build123d/topology/shape_core.py:Shape.__deepcopy__",
        "build123d/topology/shape_core.py:Shape.copy_attributes_to",
        "build123d/topology/shape_core.py:topo_distance_to",
    }
)

# What no script may reach: each is a way to a module, a frame, a namespace,
# the real builtins, a stream, or out of the child's handler.
_FORBIDDEN_KINDS = (
    types.ModuleType,
    types.FrameType,
    types.CodeType,
    types.TracebackType,
    io.IOBase,
)
_FORBIDDEN_OBJECTS = (
    builtins.getattr,
    builtins.setattr,
    builtins.delattr,
    builtins.eval,
    builtins.exec,
    builtins.compile,
    builtins.open,
    builtins.__import__,
    builtins.globals,
    builtins.locals,
    builtins.vars,
    builtins.breakpoint,
    builtins.input,
    type,
    object,
    super,
    BaseException,
    SystemExit,
    KeyboardInterrupt,
    GeneratorExit,
)


def test_build123d_s_public_names_are_the_ones_reviewed():
    # A policy lock: a release that adds a name fails here until the name is
    # reviewed and added to the list scripts are handed from -- and to the
    # withheld list as well where scripts must not get it.
    names = set(build123d.__all__)

    assert names == BUILD123D_REVIEWED, {
        "new": sorted(names - BUILD123D_REVIEWED),
        "gone": sorted(BUILD123D_REVIEWED - names),
    }


def test_every_file_function_build123d_exports_is_withheld():
    file_functions = {name for name in build123d.__all__ if _FILE_NAME.match(name)}

    assert file_functions
    assert file_functions - BUILD123D_WITHHELD == set()


def _reached(roots: list[tuple[str, object]], depth: int) -> dict[int, tuple[object, str]]:
    """Every object ``depth`` attribute reads or fewer from ``roots`` that the gate admits.

    Attribute reads only, each named as a script would have to name it, and
    each evaluating a property as a script's read would. What calling a method
    returns is not followed: there is no bound to it.
    """
    reached = {id(value): (value, path) for path, value in roots}
    frontier = list(reached.values())
    for _ in range(depth):
        following = []
        for holder, path in frontier:
            for name in dir(holder):
                if attribute_refusal(name) is not None:
                    continue
                try:
                    value = getattr(holder, name)
                except Exception:  # noqa: BLE001, S112 - a property that raises reaches nothing
                    continue
                if id(value) not in reached:
                    reached[id(value)] = (value, f"{path}.{name}")
                    following.append(reached[id(value)])
        frontier = following
    return reached


def _what_a_script_names() -> list[tuple[str, object]]:
    """Everything a script can name, and the objects a typical build makes."""
    namespace = script_globals()
    granted = namespace.pop("__builtins__")
    roots = [(f"builtins.{name}", value) for name, value in granted.items()]
    for module in ("build123d", "math", "copy"):
        view = granted["__import__"](module)
        roots += [(f"{module}.{name}", getattr(view, name)) for name in view.__all__]
    roots += list(namespace.items())
    with BuildPart() as part:
        Box(2, 2, 2)
    with BuildSketch() as sketch:
        Circle(1)
    with BuildLine() as line:
        Line((0, 0), (1, 1))
    roots += [
        ("BuildPart()", part),
        ("BuildSketch()", sketch),
        ("BuildLine()", line),
        ("Compound()", Compound(children=[Box(1, 1, 1), Pos(3, 0, 0) * Box(1, 1, 1)])),
        ("extrude(Text())", extrude(Text("A", 5), 1)),
        ("Location()", Location()),
        ("Plane.XY", Plane.XY),
        ("Vector()", Vector(1, 2, 3)),
        ("Box().bounding_box()", Box(1, 1, 1).bounding_box()),
        ("Box().edges()", Box(1, 1, 1).edges()),
    ]
    return roots


def _forbidden(value: object, module_namespaces: set[int]) -> bool:
    return (
        isinstance(value, _FORBIDDEN_KINDS)
        or any(value is f for f in _FORBIDDEN_OBJECTS)
        or (isinstance(value, dict) and id(value) in module_namespaces)
    )


def _module_namespaces() -> set[int]:
    return {id(vars(m)) for m in list(sys.modules.values()) if m is not None}


def test_nothing_a_script_can_name_leads_to_what_it_may_not_reach():
    namespaces = _module_namespaces()

    found = [
        path
        for value, path in _reached(_what_a_script_names(), depth=6).values()
        if _forbidden(value, namespaces)
    ]

    assert found == []


def test_the_census_finds_what_it_looks_for_where_it_is():
    # The walk above is only as good as its eye: from the package itself, one
    # read away, it must see the modules build123d holds.
    namespaces = _module_namespaces()

    reached = _reached([("build123d", build123d)], depth=1)

    assert any(_forbidden(value, namespaces) for value, _ in reached.values())


def _containers(reached: dict[int, tuple[object, str]]) -> dict[int, tuple[object, str]]:
    return {key: held for key, held in reached.items() if isinstance(held[0], (dict, list, set))}


def test_every_shared_table_a_script_can_reach_is_one_the_run_checks():
    # A dict, list or set reached again on a second, independent walk is shared
    # state rather than something made for this script: a script that changed
    # one in place would change it for everything read through it, so each must
    # sit on a class whose tables run_script compares before and after.
    from cadless.script_runtime import _classes_handed_out, _contents

    first = _containers(_reached(_what_a_script_names(), depth=3))
    again = _containers(_reached(_what_a_script_names(), depth=3))
    # What run_script itself records, not a second account of what it should.
    checked = {
        id(container)
        for _, containers in _contents(_classes_handed_out()).values()
        for container, _ in containers.values()
    }

    unchecked = [path for key, (_, path) in first.items() if key in again and key not in checked]

    assert unchecked == []


def _computed_access(node: ast.AST) -> bool:
    """Whether ``node`` reads or writes an attribute by a name the code does not spell."""
    if isinstance(node, ast.Attribute):
        return node.attr in ("__dict__", "__getattribute__")
    if not isinstance(node, ast.Call):
        return False
    called = (
        node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
    )
    if called in ("getattr", "setattr", "delattr", "hasattr"):
        return len(node.args) >= 2 and not isinstance(node.args[1], ast.Constant)
    return called in ("vars", "attrgetter", "methodcaller")


def _sites(package: Path, matches) -> set[str]:
    """``file:scope`` for every node under ``package`` that ``matches``."""
    sites = set()

    def visit(node: ast.AST, where: str, scope: str) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            scope = f"{scope}.{node.name}" if scope else node.name
        if matches(node):
            sites.add(f"{where}:{scope or '<module>'}")
        for child in ast.iter_child_nodes(node):
            visit(child, where, scope)

    for path in sorted(package.rglob("*.py")):
        where = path.relative_to(package.parent).as_posix()
        visit(ast.parse(path.read_text(encoding="utf-8")), where, "")
    return sites


def test_build123d_reads_attributes_by_computed_name_only_where_reviewed():
    # A policy lock on the library's own source: a new site is reviewed before
    # scripts can call into it.
    package = Path(build123d.__file__).resolve().parent

    assert _sites(package, _computed_access) == _REVIEWED_COMPUTED_ACCESS


def _executes(node: ast.AST) -> bool:
    """Whether ``node`` calls ``exec`` or ``eval``, by name or as an attribute."""
    if not isinstance(node, ast.Call):
        return False
    called = (
        node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
    )
    return called in ("exec", "eval")


def test_only_the_script_runtime_executes_code():
    # Any other exec would run a script without the gate and the namespace.
    root = Path(cadless.__file__).resolve().parent.parent
    sites = set()
    for package in ("cadless", "backend", "worker"):
        sites |= _sites(root / package, _executes)

    assert sites == {"cadless/script_runtime.py:run_script"}
