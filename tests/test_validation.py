"""Static-validation tests."""

from pathlib import Path

import pytest

from cadless.api_subset import ALLOWED_EXAMPLE, REJECTED_EXAMPLE
from cadless.few_shot import FEW_SHOT
from cadless.validation import validate_code

_CATALOG = Path(__file__).resolve().parents[1] / "catalog"
_CATALOG_STEPS = sorted(_CATALOG.glob("*/*/steps/*.py"))


def _refused_for(code: str, reason: str) -> bool:
    """Whether the gate refuses ``code`` with a reason that starts with ``reason``."""
    res = validate_code(code)
    return not res.ok and any(r.startswith(reason) for r in res.reasons)


def test_allowed_example_passes():
    res = validate_code(ALLOWED_EXAMPLE)
    assert res.ok, res.reasons


def test_all_few_shot_pass_validation():
    for ex in FEW_SHOT:
        assert validate_code(ex.code).ok, (ex.prompt, validate_code(ex.code).reasons)


def test_rejected_example_fails_for_import_and_more():
    res = validate_code(REJECTED_EXAMPLE)
    assert not res.ok
    assert any("disallowed import: os" in r for r in res.reasons)


def test_the_helper_a_script_is_handed_is_not_importable():
    # Scripts get split_and_join in their namespace; importing it would need the
    # whole cadless package on the allow-list.
    res = validate_code("from cadless.joinery import split_and_join\nresult = 1\n")
    assert not res.ok
    assert any(r.startswith("disallowed import") and "cadless.joinery" in r for r in res.reasons)


def test_banned_open_call():
    res = validate_code("from build123d import *\nopen('/etc/passwd')\nresult = Box(1,1,1)")
    assert not res.ok
    assert any("open" in r for r in res.reasons)


def test_dunder_escape_blocked():
    code = "result = ().__class__.__bases__[0].__subclasses__()"
    res = validate_code(code)
    assert not res.ok
    assert any("dunder" in r for r in res.reasons)


def test_missing_result_fails():
    res = validate_code("from build123d import *\nx = Box(1,1,1)")
    assert not res.ok
    assert any("result" in r for r in res.reasons)


def test_tuple_unpacking_result_ok():
    res = validate_code("from build123d import *\nresult, leftover = Box(1,1,1), 0")
    assert res.ok, res.reasons


def test_syntax_error_reported():
    res = validate_code("from build123d import *\nresult = Box(1,1,")
    assert not res.ok
    assert any("syntax error" in r for r in res.reasons)


def test_eval_and_import_dunder_banned():
    res = validate_code("result = eval('1')\n__import__('os')")
    assert not res.ok
    assert any("eval" in r for r in res.reasons)
    assert any("__import__" in r for r in res.reasons)


def test_bool_protocol():
    assert bool(validate_code(ALLOWED_EXAMPLE)) is True
    assert bool(validate_code("x = 1")) is False


# --- what a script may reach, by name ------------------------------------------


@pytest.mark.parametrize(
    "attribute", ["gi_frame", "cr_frame", "ag_frame", "f_back", "tb_frame", "co_consts"]
)
def test_frame_generator_and_code_attributes_are_refused(attribute):
    code = f"g = (x for x in [1])\nresult = g.{attribute}\n"
    assert _refused_for(code, f"interpreter-internals attribute access: .{attribute}")


def test_private_attributes_are_refused():
    code = "from build123d import *\nresult = Box(1, 1, 1)._anything\n"
    assert _refused_for(code, "private attribute access: ._anything")


@pytest.mark.parametrize("method", ["format", "format_map"])
def test_string_driven_attribute_lookup_is_refused(method):
    code = f"result = '{{0}}'.{method}(1)\n"
    assert _refused_for(code, f"string-driven attribute lookup: .{method}")


@pytest.mark.parametrize("attribute", ["mro", "copy_attributes_to"])
def test_attributes_that_reach_past_the_policy_are_refused(attribute):
    code = f"from build123d import *\nresult = Box(1, 1, 1).{attribute}\n"
    assert _refused_for(code, f"attribute not available to scripts: .{attribute}")


@pytest.mark.parametrize(
    "code",
    [
        "x = __builtins__\nresult = 1\n",
        "import math as __builtins__\nresult = 1\n",
        "def __getattr__(name):\n    return 1\nresult = 1\n",
        "def f(__x__):\n    return 1\nresult = 1\n",
        "result = dict(__x__=1)\n",
        "def f():\n    global __x__\nresult = 1\n",
        "try:\n    result = 1\nexcept Exception as __x__:\n    pass\n",
        "async def __x__():\n    pass\nresult = 1\n",
        "def f():\n    x = 1\n    def g():\n        nonlocal __x__\nresult = 1\n",
        "def f[__x__](a):\n    return a\nresult = 1\n",
    ],
    ids=[
        "a name",
        "an import alias",
        "a function",
        "a parameter",
        "a keyword",
        "a global",
        "an exception",
        "an async function",
        "a nonlocal",
        "a type parameter",
    ],
)
def test_dunder_names_are_refused_wherever_a_name_is_written(code):
    assert _refused_for(code, "dunder name: __")


def test_banned_names_are_refused_wherever_a_name_is_written():
    assert _refused_for("def open(path):\n    return path\nresult = 1\n", "banned name: open")


def test_a_banned_name_cannot_be_imported():
    assert _refused_for("from math import eval\nresult = 1\n", "banned name: eval")


@pytest.mark.parametrize(
    ("code", "name"),
    [
        ("result = type(1)\n", "type"),
        ("result = hasattr(1, 'real')\n", "hasattr"),
        ("try:\n    result = 1\nexcept SystemExit:\n    result = 2\n", "SystemExit"),
    ],
    ids=["a class of anything", "a lookup by name", "an exception past Exception"],
)
def test_a_builtin_scripts_are_not_given_is_refused_by_name(code, name):
    assert _refused_for(code, f"builtin not available to scripts: {name}")


@pytest.mark.parametrize(
    "code",
    [
        "dir = 1\nresult = dir + 1\n",
        "def f(id):\n    return id\nresult = f(1)\n",
        "import math as id\nresult = id.pi\n",
        "def type():\n    return 1\nresult = type()\n",
        "try:\n    result = 1\nexcept Exception as id:\n    result = id\n",
    ],
    ids=["a variable", "a parameter", "an import alias", "a function", "an exception"],
)
def test_a_script_may_define_a_name_a_builtin_also_has(code):
    res = validate_code(code)
    assert res.ok, res.reasons


def test_an_annotation_may_name_any_builtin():
    # Scripts are compiled with annotations postponed: nothing in one is looked up.
    code = "x: object = 1\ndef f(kind: type = float) -> object:\n    return kind\nresult = f()\n"
    res = validate_code(code)
    assert res.ok, res.reasons


@pytest.mark.parametrize(
    ("code", "reason"),
    [
        ("from build123d import os\nresult = 1\n", "not available to scripts: os from build123d"),
        (
            "from build123d import Shape\nresult = 1\n",
            "not available to scripts: Shape from build123d",
        ),
        (
            "from copy import dispatch_table\nresult = 1\n",
            "not available to scripts: dispatch_table from copy",
        ),
    ],
    ids=["a module build123d holds", "a name build123d does not list", "copy's own table"],
)
def test_a_name_taken_from_a_module_is_one_reviewed_for_scripts(code, reason):
    assert _refused_for(code, reason)


def test_a_script_too_deep_to_read_is_refused_rather_than_raised():
    code = "result = " + "+".join(["1"] * 100_000) + "\n"
    assert _refused_for(code, "syntax error: nested too deeply to check")


def test_a_script_the_parser_cannot_encode_is_refused_rather_than_raised():
    # A lone surrogate travels in JSON and stops the parser with a ValueError.
    code = 'result = "' + chr(0xD800) + '"\n'
    assert _refused_for(code, "syntax error: unreadable source")


@pytest.mark.parametrize(
    ("code", "reason"),
    [
        ("import build123d.topology\nresult = 1\n", "disallowed import: build123d.topology"),
        (
            "from build123d.topology import Solid\nresult = 1\n",
            "disallowed import: from build123d.topology",
        ),
        ("from .build123d import Box\nresult = 1\n", "disallowed import: from .build123d"),
        ("from . import build123d\nresult = 1\n", "disallowed import: from ."),
    ],
    ids=["a submodule", "from a submodule", "relative", "relative, no module"],
)
def test_submodule_and_relative_imports_are_refused(code, reason):
    assert _refused_for(code, reason)


def test_private_names_cannot_be_imported():
    code = "from build123d import _private\nresult = 1\n"
    assert _refused_for(code, "private import: _private from build123d")


@pytest.mark.parametrize(
    ("code", "reason"),
    [
        ("from build123d import export_stl\nresult = 1\n", "withheld name: export_stl"),
        (
            "from build123d import *\nresult = Box(1, 1, 1)\nexport_to_pcbway(result)\n",
            "withheld name: export_to_pcbway",
        ),
        ("import build123d\nresult = build123d.import_step\n", "withheld attribute: .import_step"),
        ("from build123d import *\nresult = FontManager()\n", "withheld name: FontManager"),
    ],
    ids=["imported", "after a star import", "as an attribute", "the font manager"],
)
def test_file_network_and_font_functions_are_refused(code, reason):
    assert _refused_for(code, reason)


@pytest.mark.parametrize(
    "code",
    [
        "from build123d import *\nresult = Box(1, 1, 1)\nresult.bounding_box = lambda: 1\n",
        "from build123d import *\nBox.volume += 1\nresult = Box(1, 1, 1)\n",
        "from build123d import *\nresult = Box(1, 1, 1)\ndel result.joints\n",
    ],
    ids=["set", "set in place", "deleted"],
)
def test_a_script_writes_no_attribute_but_those_that_name_colour_and_place_a_part(code):
    assert _refused_for(code, "attribute write not allowed: .")


def test_a_part_s_name_colour_and_placement_may_be_set():
    code = (
        "from build123d import *\nresult = Box(1, 1, 1)\n"
        "result.label = 'lid'\nresult.color = Color(1, 0, 0)\n"
        "result.location = Location((5, 0, 0))\n"
    )
    res = validate_code(code)
    assert res.ok, res.reasons


def test_class_definitions_are_refused():
    assert _refused_for("class Part:\n    pass\nresult = 1\n", "class definitions are not allowed")


def test_match_statements_are_refused():
    code = "match 1:\n    case 1:\n        result = 1\n"
    assert _refused_for(code, "match statements are not allowed")


def test_the_bundled_catalog_has_steps_to_check():
    # Without this, a moved catalog would leave the check below passing on no
    # steps at all.
    assert _CATALOG_STEPS


@pytest.mark.parametrize("step", _CATALOG_STEPS, ids=lambda p: str(p.relative_to(_CATALOG)))
def test_every_bundled_catalog_step_passes_the_gate(step):
    res = validate_code(step.read_text())
    assert res.ok, res.reasons
