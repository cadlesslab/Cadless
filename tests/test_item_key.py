"""The item key library: canonical forms and the keys built from them."""

from __future__ import annotations

import ast
import math
import struct
from pathlib import Path

import pytest

from cadless import item_key
from cadless.item_key import (
    HELPER_SOURCES,
    ItemKeyError,
    canonical_params,
    canonical_pose,
    canonical_recipe,
    helpers_called,
    jcs,
    typed_params,
)
from cadless.params import PARAMS_VARIABLE


def _double(bits: str) -> float:
    return struct.unpack(">d", bytes.fromhex(bits))[0]


# RFC 8785 appendix B: an IEEE 754 bit pattern and the JSON text it serialises to.
RFC_NUMBERS = [
    ("0000000000000000", "0"),
    ("8000000000000000", "0"),
    ("0000000000000001", "5e-324"),
    ("8000000000000001", "-5e-324"),
    ("7fefffffffffffff", "1.7976931348623157e+308"),
    ("ffefffffffffffff", "-1.7976931348623157e+308"),
    ("4340000000000000", "9007199254740992"),
    ("c340000000000000", "-9007199254740992"),
    ("4430000000000000", "295147905179352830000"),
    ("44b52d02c7e14af5", "9.999999999999997e+22"),
    ("44b52d02c7e14af6", "1e+23"),
    ("44b52d02c7e14af7", "1.0000000000000001e+23"),
    ("444b1ae4d6e2ef4e", "999999999999999700000"),
    ("444b1ae4d6e2ef4f", "999999999999999900000"),
    ("444b1ae4d6e2ef50", "1e+21"),
    ("3eb0c6f7a0b5ed8c", "9.999999999999997e-7"),
    ("3eb0c6f7a0b5ed8d", "0.000001"),
    ("41b3de4355555553", "333333333.3333332"),
    ("41b3de4355555554", "333333333.33333325"),
    ("41b3de4355555555", "333333333.3333333"),
    ("41b3de4355555556", "333333333.3333334"),
    ("41b3de4355555557", "333333333.33333343"),
    ("becbf647612f3696", "-0.0000033333333333333333"),
    ("43143ff3c1cb0959", "1424953923781206.2"),
]


@pytest.mark.parametrize(("bits", "text"), RFC_NUMBERS)
def test_numbers_follow_the_rfc_8785_examples(bits: str, text: str) -> None:
    assert jcs(_double(bits)) == text.encode()


@pytest.mark.parametrize(
    ("value", "text"),
    [(2.0, "2"), (4.5, "4.5"), (0.002, "0.002"), (1e-7, "1e-7"), (1e21, "1e+21"), (-1.0, "-1")],
)
def test_a_float_is_written_in_its_shortest_ecmascript_form(value: float, text: str) -> None:
    assert jcs(value) == text.encode()


def test_integers_are_written_as_integers_up_to_the_safe_limit() -> None:
    assert jcs(0) == b"0"
    assert jcs(-7) == b"-7"
    assert jcs(2**53 - 1) == b"9007199254740991"
    assert jcs(-(2**53 - 1)) == b"-9007199254740991"


@pytest.mark.parametrize("value", [2**53, -(2**53), 10**30])
def test_an_integer_past_the_safe_limit_is_refused(value: int) -> None:
    with pytest.raises(ItemKeyError, match="integer"):
        jcs(value)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_numbers_are_refused(value: float) -> None:
    with pytest.raises(ItemKeyError, match="finite"):
        jcs(value)


def test_literals() -> None:
    assert jcs(True) == b"true"
    assert jcs(False) == b"false"
    assert jcs(None) == b"null"


def test_keys_are_sorted_by_utf16_code_units_as_in_rfc_8785() -> None:
    # The RFC's own example: by code units the emoji (a surrogate pair starting
    # 0xD83D) sorts before U+FB33, the opposite of code point order.
    value = {
        "€": "Euro Sign",
        "\r": "Carriage Return",
        "דּ": "Hebrew Letter Dalet With Dagesh",
        "1": "One",
        "\U0001f600": "Emoji: Grinning Face",
        "\u0080": "Control",
        "ö": "Latin Small Letter O With Diaeresis",
    }
    order = ["\r", "1", "\u0080", "ö", "€", "\U0001f600", "דּ"]
    expected = "{" + ",".join(f'"{_escaped(k)}":"{value[k]}"' for k in order) + "}"
    assert jcs(value) == expected.encode()


def _escaped(key: str) -> str:
    return "\\r" if key == "\r" else key


def test_strings_escape_only_what_json_requires() -> None:
    text = 'a"b\\c\b\f\n\r\t\x00\x1fé€/'
    assert jcs(text) == '"a\\"b\\\\c\\b\\f\\n\\r\\t\\u0000\\u001fé€/"'.encode()


def test_a_lone_surrogate_is_refused() -> None:
    with pytest.raises(ItemKeyError, match="surrogate"):
        jcs("\ud800")


def test_containers_are_written_without_whitespace() -> None:
    assert jcs({"b": [1, 2.5, "x"], "a": {"z": None, "y": True}}) == (
        b'{"a":{"y":true,"z":null},"b":[1,2.5,"x"]}'
    )
    assert jcs((1, 2)) == b"[1,2]"
    assert jcs({}) == b"{}"
    assert jcs([]) == b"[]"


def test_a_non_string_key_is_refused() -> None:
    with pytest.raises(ItemKeyError, match="key"):
        jcs({1: "one"})


@pytest.mark.parametrize("value", [b"bytes", {1, 2}, object(), 1j])
def test_an_unsupported_type_is_refused(value: object) -> None:
    with pytest.raises(ItemKeyError, match="cannot"):
        jcs(value)


# --- typed params -----------------------------------------------------------


def test_params_are_written_with_their_declared_types() -> None:
    assert canonical_params({"b": 2, "a": 1.5}, {"a": "float", "b": "int"}) == (
        b'{"a":{"type":"float","value":1.5},"b":{"type":"int","value":2}}'
    )


def test_the_declared_type_is_part_of_the_bytes() -> None:
    # JSON writes 4 and 4.0 alike, so the declaration is what tells them apart.
    assert canonical_params({"n": 4}, {"n": "int"}) != canonical_params({"n": 4}, {"n": "float"})


def test_an_undeclared_parameter_is_refused() -> None:
    with pytest.raises(ItemKeyError, match="no declared type"):
        canonical_params({"a": 1.0, "b": 2.0}, {"a": "float"})


def test_a_declared_parameter_without_a_value_is_refused() -> None:
    with pytest.raises(ItemKeyError, match="no value"):
        canonical_params({"a": 1.0}, {"a": "float", "b": "float"})


def test_an_unknown_type_is_refused() -> None:
    with pytest.raises(ItemKeyError, match="type"):
        canonical_params({"a": 1}, {"a": "number"})


def test_an_integral_float_is_accepted_for_an_int() -> None:
    assert canonical_params({"n": 2.0}, {"n": "int"}) == canonical_params({"n": 2}, {"n": "int"})


def test_a_non_integral_float_is_refused_for_an_int() -> None:
    with pytest.raises(ItemKeyError, match="integer"):
        canonical_params({"n": 2.5}, {"n": "int"})


def test_an_int_is_accepted_for_a_float() -> None:
    assert canonical_params({"d": 2}, {"d": "float"}) == canonical_params(
        {"d": 2.0}, {"d": "float"}
    )


def test_a_recipe_receives_each_value_as_its_declared_type() -> None:
    typed = typed_params(
        {"d": 2, "n": 3.0, "s": "x", "f": False},
        {"d": "float", "n": "int", "s": "str", "f": "bool"},
    )
    assert typed == {"d": 2.0, "n": 3, "s": "x", "f": False}
    assert type(typed["d"]) is float
    assert type(typed["n"]) is int


@pytest.mark.parametrize("declared", ["int", "float"])
def test_a_bool_is_never_a_number(declared: str) -> None:
    with pytest.raises(ItemKeyError, match="bool"):
        canonical_params({"n": True}, {"n": declared})


def test_a_number_is_never_a_bool() -> None:
    with pytest.raises(ItemKeyError, match="bool"):
        canonical_params({"flag": 1}, {"flag": "bool"})


def test_a_non_string_is_refused_for_a_str() -> None:
    with pytest.raises(ItemKeyError, match="str"):
        canonical_params({"label": 3}, {"label": "str"})


def test_a_non_finite_float_is_refused() -> None:
    with pytest.raises(ItemKeyError, match="finite"):
        canonical_params({"d": float("nan")}, {"d": "float"})


def test_strings_are_not_normalised() -> None:
    # Precomposed and decomposed forms of the same letter stay two inputs.
    nfc = canonical_params({"s": "é"}, {"s": "str"})
    nfd = canonical_params({"s": "é"}, {"s": "str"})
    assert nfc != nfd


# --- canonical pose ---------------------------------------------------------


def test_the_identity_pose() -> None:
    assert canonical_pose((0, 0, 0, 0, 0, 0, 1)) == (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0)


def test_q_and_minus_q_give_one_pose() -> None:
    q = (10.0, -5.0, 2.5, 0.0, 0.6, 0.0, 0.8)
    minus_q = (10.0, -5.0, 2.5, -0.0, -0.6, -0.0, -0.8)
    assert canonical_pose(q) == canonical_pose(minus_q) == (10.0, -5.0, 2.5, 0.0, 0.6, 0.0, 0.8)


def test_no_number_of_a_flipped_pose_is_negative_zero() -> None:
    pose = canonical_pose((0.0, 0.0, 0.0, 0.0, -0.6, 0.0, -0.8))
    assert all(math.copysign(1.0, number) == 1.0 for number in pose if number == 0)


def test_with_qw_zero_the_first_non_zero_component_is_positive() -> None:
    assert canonical_pose((0, 0, 0, -1, 0, 0, 0)) == canonical_pose((0, 0, 0, 1, 0, 0, 0))
    assert canonical_pose((0, 0, 0, -1, 0, 0, 0))[3] == 1.0


def test_the_sign_rule_reads_the_rounded_quaternion() -> None:
    # qw rounds to zero, so the sign follows qx whichever way qw leaned.
    leaning_negative = canonical_pose((0, 0, 0, 0.6, 0.8, 0.0, -4e-7))
    leaning_positive = canonical_pose((0, 0, 0, -0.6, -0.8, 0.0, 4e-7))
    assert leaning_negative == leaning_positive == (0.0, 0.0, 0.0, 0.6, 0.8, 0.0, 0.0)


def test_a_nearly_unit_quaternion_is_normalised() -> None:
    assert canonical_pose((0, 0, 0, 0, 0, 0, 1.0000005)) == (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0)


def test_a_quaternion_off_unit_length_is_refused() -> None:
    with pytest.raises(ItemKeyError, match="unit"):
        canonical_pose((0, 0, 0, 0, 0, 0, 1.00001))


def test_every_number_is_rounded_to_one_millionth() -> None:
    assert canonical_pose((1.23456789, -2.0000004, 3.0, 0, 0, 0, 1))[:3] == (1.234568, -2.0, 3.0)


def test_negative_zero_is_written_as_zero() -> None:
    pose = canonical_pose((-0.0, -1e-9, 0.0, 0, 0, 0, 1))
    assert pose[:2] == (0.0, 0.0)
    assert math.copysign(1.0, pose[0]) == 1.0
    assert math.copysign(1.0, pose[1]) == 1.0


@pytest.mark.parametrize("pose", [(0, 0, 0, 0, 0, 1), (0, 0, 0, 0, 0, 0, 1, 0)])
def test_a_pose_of_the_wrong_length_is_refused(pose: tuple) -> None:
    with pytest.raises(ItemKeyError, match="seven"):
        canonical_pose(pose)


@pytest.mark.parametrize(
    "pose",
    [(float("nan"), 0, 0, 0, 0, 0, 1), (0, 0, 0, 0, 0, 0, float("inf")), (True, 0, 0, 0, 0, 0, 1)],
)
def test_a_pose_holding_a_non_number_is_refused(pose: tuple) -> None:
    with pytest.raises(ItemKeyError):
        canonical_pose(pose)


# --- canonical recipe -------------------------------------------------------

PLATE = (
    '"""A plate with a hole."""\n'
    "from build123d import *\n"
    "\n"
    'params = {"width": 40, "height": 20.5, "label": "A"}  # mm\n'
    "\n"
    "\n"
    "def hole(d):\n"
    '    """One hole."""\n'
    "    return Cylinder(d / 2, 3)\n"
    "\n"
    "\n"
    'part = Box(params["width"], params["height"], 3) - hole(6)\n'
)


def test_the_canonical_recipe_text_is_exact() -> None:
    assert canonical_recipe(PLATE) == (
        b"from build123d import *\n"
        b"params = {'width': ..., 'height': ..., 'label': ...}\n"
        b"\n"
        b"def hole(d):\n"
        b"    return Cylinder(d / 2, 3)\n"
        b"part = Box(params['width'], params['height'], 3) - hole(6)"
    )


def test_comments_layout_and_docstrings_do_not_change_the_recipe_text() -> None:
    rewritten = (
        "from build123d import *  # every name\n"
        "params = {'width': 40,\n"
        "          'height': 20.5, 'label': 'A'}\n"
        "def hole(d):\n"
        "    return Cylinder(d/2, 3)  # radius, height\n"
        "part = (Box(params['width'], params['height'], 3)\n"
        "        - hole(6))\n"
    )
    assert canonical_recipe(rewritten) == canonical_recipe(PLATE)


def test_params_values_do_not_change_the_recipe_text() -> None:
    other = PLATE.replace(
        '"width": 40, "height": 20.5, "label": "A"', '"width": 9, "height": 7, "label": "B"'
    )
    assert canonical_recipe(other) == canonical_recipe(PLATE)


def test_the_params_names_stay_in_the_recipe_text_in_order() -> None:
    # A recipe can loop over params, so the order of its names can change the shape.
    reordered = PLATE.replace('"width": 40, "height": 20.5', '"height": 20.5, "width": 40')
    assert canonical_recipe(reordered) != canonical_recipe(PLATE)


def test_the_params_literal_keeps_its_place() -> None:
    # A statement above the literal runs before params exists, so where the
    # literal stands can change what runs.
    above = "params = {'w': 1}\nx = 2\n"
    below = "x = 2\nparams = {'w': 1}\n"
    assert canonical_recipe(above) != canonical_recipe(below)


def test_a_change_to_params_after_the_literal_stays_in_the_text() -> None:
    plain = "params = {'w': 1}\n"
    assert canonical_recipe(plain + "params['w'] = 2\n") != canonical_recipe(plain)


def test_every_docstring_is_dropped_and_an_emptied_body_keeps_a_pass() -> None:
    source = (
        '"""Module."""\n'
        'params = {"w": 1}\n'
        "class Plate:\n"
        '    """Class."""\n'
        "def f():\n"
        '    """Function."""\n'
        "async def g():\n"
        '    """Coroutine."""\n'
        "    return 1\n"
    )
    assert canonical_recipe(source) == (
        b"params = {'w': ...}\n"
        b"\n"
        b"class Plate:\n"
        b"    pass\n"
        b"\n"
        b"def f():\n"
        b"    pass\n"
        b"\n"
        b"async def g():\n"
        b"    return 1"
    )


def test_a_string_that_is_not_a_docstring_is_kept() -> None:
    plain = "params = {'w': 1}\nx = 2\n"
    assert canonical_recipe(plain + "'a note'\n") != canonical_recipe(plain)


@pytest.mark.parametrize("body", ["...", "return 'x'"])
def test_a_first_statement_that_is_not_a_lone_string_is_kept(body: str) -> None:
    source = f"params = {{}}\ndef f():\n    {body}\n"
    assert canonical_recipe(source) == f"params = {{}}\n\ndef f():\n    {body}".encode()


def test_the_params_literal_is_the_one_the_engine_reads() -> None:
    source = f"{PARAMS_VARIABLE} = {{'w': 1}}\n"
    assert canonical_recipe(source) == f"{PARAMS_VARIABLE} = {{'w': ...}}".encode()


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("", id="empty"),
        pytest.param("x = 1\n", id="none"),
        pytest.param("params = {'w': 1}\nparams = {'w': 2}\n", id="two"),
        pytest.param("if True:\n    params = {'w': 1}\n", id="inside-a-block"),
        pytest.param("params = {'w': 1}\nparams: int = 3\n", id="annotated-and-plain"),
        pytest.param("params = 5\n", id="not-a-dict"),
        pytest.param("params = dict(w=1)\n", id="not-a-dict-literal"),
        pytest.param("params = {'w': w0}\n", id="value-not-a-literal"),
        pytest.param("params = {'w': 1}\nparams = dict(params)\n", id="bound-again"),
        pytest.param("params, x = {'w': 1}, 2\n", id="unpacked"),
        pytest.param("*params, = {'w': 1}\n", id="starred"),
        pytest.param("params = q = {'w': 1}\n", id="chained"),
        pytest.param("params = {1: 2}\n", id="name-not-a-string"),
        pytest.param("params = {[1]: 2}\n", id="name-unhashable"),
        pytest.param("params = {'w': (1, 2)}\n", id="value-a-tuple"),
        pytest.param("params = {'w': None}\n", id="value-none"),
    ],
)
def test_a_recipe_without_exactly_one_plain_params_literal_is_refused(source: str) -> None:
    with pytest.raises(ItemKeyError, match="params"):
        canonical_recipe(source)


def test_an_annotated_params_is_refused_as_such() -> None:
    with pytest.raises(ItemKeyError, match="annotates"):
        canonical_recipe("params: dict = {'w': 1}\n")


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("params = {'w': 1}\ndef (", id="syntax"),
        pytest.param("params = {'w': 1}\x00", id="null-byte"),
        pytest.param("params = {'w': '\ud800'}", id="lone-surrogate"),
        pytest.param("params = {}\nx = " + "+".join(["1"] * 2_000), id="too-deep-to-write"),
        pytest.param("params = {}\nx = " + "+".join(["1"] * 100_000), id="too-deep-to-parse"),
    ],
)
def test_a_recipe_python_cannot_parse_or_write_back_is_refused(source: str) -> None:
    with pytest.raises(ItemKeyError, match="recipe"):
        canonical_recipe(source)


def test_a_recipe_is_text() -> None:
    with pytest.raises(ItemKeyError, match="text"):
        canonical_recipe(b"params = {'w': 1}\n")


# --- helpers called ---------------------------------------------------------


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("a, b = split_and_join(part, plane, axis, 0.2)\n", id="called"),
        pytest.param("join = split_and_join\n", id="named"),
        pytest.param("h = [split_and_join(p, plane, axis, 0.2) for p in parts]\n", id="nested"),
        pytest.param("def cut(p):\n    return split_and_join(p, plane, axis, 0.2)\n", id="in-def"),
    ],
)
def test_a_helper_is_found_wherever_the_recipe_loads_its_name(source: str) -> None:
    assert helpers_called(source) == frozenset({"split_and_join"})


def test_a_helper_the_recipe_binds_itself_still_counts() -> None:
    # Telling a recipe's own function from the engine's is not worth the risk of
    # leaving the helper's source out of the key, so a load always counts.
    source = "def split_and_join(*a):\n    return a\nsplit_and_join(1)\n"
    assert helpers_called(source) == frozenset({"split_and_join"})


def test_only_a_load_of_the_bare_name_counts() -> None:
    source = (
        "my_split_and_join = 1\n"
        "y = my_split_and_join\n"
        "x = obj.split_and_join\n"
        "note = 'split_and_join'\n"
        "split_and_join = 2\n"
    )
    assert helpers_called(source) == frozenset()


def test_helpers_called_refuses_a_source_python_cannot_parse() -> None:
    with pytest.raises(ItemKeyError, match="recipe"):
        helpers_called("def (")


def test_every_helper_a_script_is_handed_has_its_sources_listed() -> None:
    # Imported here: joinery imports build123d, which the rest of this file does not need.
    from cadless.joinery import script_namespace

    assert set(HELPER_SOURCES) == set(script_namespace())


def test_every_listed_helper_source_is_a_file_beside_the_module() -> None:
    package = Path(item_key.__file__).parent
    for files in HELPER_SOURCES.values():
        for name in files:
            assert (package / name).is_file(), name


def test_a_helpers_listed_sources_hold_every_engine_module_they_import() -> None:
    # A helper's output can change through any engine module it imports, so each
    # listed file's engine imports must be listed too.
    package = Path(item_key.__file__).parent
    for helper, files in HELPER_SOURCES.items():
        listed = {name.removesuffix(".py") for name in files}
        for name in files:
            imported = _engine_imports(ast.parse((package / name).read_text()))
            assert imported <= listed, f"{helper}: {name} imports {sorted(imported - listed)}"


def _engine_imports(tree: ast.Module) -> set[str]:
    # The cadless modules a source imports, by their names within the package.
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {a.name for a in node.names if a.name.startswith("cadless.")}
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level == 0 and module != "cadless" and not module.startswith("cadless."):
                continue
            if node.level == 0 and module.startswith("cadless."):
                found.add(module)
            else:  # from cadless import x, from . import x, from .x import y
                base = module.removeprefix("cadless").lstrip(".")
                found |= {base} if base else {a.name for a in node.names}
    return {name.removeprefix("cadless.") for name in found}
