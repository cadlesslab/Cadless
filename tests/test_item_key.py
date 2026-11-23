"""The item key library: canonical forms and the keys built from them."""

from __future__ import annotations

import ast
import enum
import hashlib
import json
import math
import os
import platform
import random
import re
import shutil
import struct
import subprocess
import sys
import warnings
from collections.abc import Callable
from importlib import metadata
from pathlib import Path

import pytest

import cadless
from cadless import item_key as item_key_module
from cadless import params as params_module
from cadless.item_key import (
    DEFINITIONS_VERSION,
    HELPER_SOURCES,
    RUN_AND_BAKE_FILES,
    ItemKeyError,
    canonical_params,
    canonical_pose,
    canonical_recipe,
    helpers_called,
    item_key,
    jcs,
    leaf_key,
    toolchain_identity,
    typed_params,
)
from cadless.params import PARAMS_VARIABLE


def _double(bits: str) -> float:
    return struct.unpack(">d", bytes.fromhex(bits))[0]


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


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (2**53, "9007199254740992"),
        (-(2**53), "-9007199254740992"),
        (2**60, "1152921504606847000"),
        (10**22, "1e+22"),
    ],
)
def test_an_integer_a_double_holds_exactly_is_written_as_that_double(value: int, text: str) -> None:
    # Written in the shortest form that reads back as the same double, as 2.0**60 is.
    assert jcs(value) == text.encode() == jcs(float(value))


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(2**53 + 1, id="2^53+1"),
        pytest.param(-(2**53 + 1), id="-(2^53+1)"),
        pytest.param(10**30, id="10^30"),
        pytest.param(10**400, id="10^400"),
        pytest.param(-(10**5000), id="-(10^5000)"),
    ],
)
def test_an_integer_no_double_holds_exactly_is_refused(value: int) -> None:
    with pytest.raises(ItemKeyError, match="integer"):
        jcs(value)


class _Loud(float):
    def __repr__(self) -> str:
        return "loud"


class _Count(enum.IntEnum):
    TWO = 2


class _Shout(str):
    def __str__(self) -> str:
        return "SHOUT"

    def __iter__(self):  # lies about its characters too
        return iter("SHOUT")


def test_a_subclass_is_written_as_the_plain_value_it_stands_for() -> None:
    assert jcs([_Loud(1.5), _Count.TWO, _Shout("a")]) == b'[1.5,2,"a"]'


class _Twin(str):
    # Hashes apart from the plain string, so a dict holds both as keys.
    def __hash__(self) -> int:
        return 1


def test_two_keys_that_are_the_same_string_are_refused() -> None:
    with pytest.raises(ItemKeyError, match="twice"):
        jcs({"a": 1, _Twin("a"): 2})


def _nested(levels: int) -> object:
    value: object = 0
    for _ in range(levels):
        value = [value]
    return value


def test_a_value_nested_deeper_than_the_limit_is_refused() -> None:
    assert jcs(_nested(100)) == b"[" * 100 + b"0" + b"]" * 100
    with pytest.raises(ItemKeyError, match="nested"):
        jcs(_nested(101))


def test_a_container_that_holds_itself_is_refused() -> None:
    loop: list = []
    loop.append(loop)
    with pytest.raises(ItemKeyError, match="nested"):
        jcs(loop)


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
        "\u20ac": "Euro Sign",
        "\r": "Carriage Return",
        "\ufb33": "Hebrew Letter Dalet With Dagesh",
        "1": "One",
        "\U0001f600": "Emoji: Grinning Face",
        "\u0080": "Control",
        "\u00f6": "Latin Small Letter O With Diaeresis",
    }
    order = ["\r", "1", "\u0080", "\u00f6", "\u20ac", "\U0001f600", "\ufb33"]
    expected = "{" + ",".join(f'"{_escaped(k)}":"{value[k]}"' for k in order) + "}"
    assert jcs(value) == expected.encode()


def _escaped(key: str) -> str:
    return "\\r" if key == "\r" else key


def test_strings_escape_only_what_json_requires() -> None:
    text = 'a"b\\c\b\f\n\r\t\x00\x1f\u00e9\u20ac/'
    assert jcs(text) == '"a\\"b\\\\c\\b\\f\\n\\r\\t\\u0000\\u001f\u00e9\u20ac/"'.encode()


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


def test_a_negative_zero_param_runs_as_the_zero_its_key_holds() -> None:
    # Canonical JSON writes -0.0 as 0, and atan2(-0.0, -1) is not atan2(0.0, -1).
    typed = typed_params({"d": -0.0}, {"d": "float"})
    assert math.copysign(1.0, typed["d"]) == 1.0


def test_a_param_given_as_a_subclass_runs_as_a_plain_value() -> None:
    typed = typed_params(
        {"d": _Loud(1.5), "n": _Count.TWO, "s": _Shout("a")},
        {"d": "float", "n": "int", "s": "str"},
    )
    assert typed == {"d": 1.5, "n": 2, "s": "a"}
    assert [type(value) for value in typed.values()] == [float, int, str]


@pytest.mark.parametrize(("params", "types"), [(None, {}), ({}, None), ([("a", 1)], {"a": "int"})])
def test_params_and_types_must_be_mappings(params: object, types: object) -> None:
    with pytest.raises(ItemKeyError, match="mapping"):
        typed_params(params, types)


def test_an_int_too_large_for_a_float_is_refused_where_a_float_is_declared() -> None:
    with pytest.raises(ItemKeyError, match="too large"):
        typed_params({"d": 10**400}, {"d": "float"})


def test_an_int_no_double_holds_is_refused_as_a_param() -> None:
    with pytest.raises(ItemKeyError, match="integer"):
        canonical_params({"n": 10**400}, {"n": "int"})


@pytest.mark.parametrize(
    ("params", "types"),
    [
        pytest.param({"n": 2**53 + 1}, {"n": "int"}, id="int-no-double-holds"),
        pytest.param({"s": "\ud800"}, {"s": "str"}, id="lone-surrogate"),
        pytest.param({1: 2.0}, {1: "float"}, id="name-not-a-string"),
    ],
)
def test_typed_params_refuses_what_could_not_be_keyed(params: dict, types: dict) -> None:
    with pytest.raises(ItemKeyError):
        typed_params(params, types)


def test_strings_are_not_normalised() -> None:
    # Precomposed and decomposed forms of the same letter stay two inputs.
    nfc = canonical_params({"s": "\u00e9"}, {"s": "str"})
    nfd = canonical_params({"s": "e\u0301"}, {"s": "str"})
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


def test_with_qw_and_qx_zero_the_sign_follows_qy() -> None:
    assert canonical_pose((0, 0, 0, 0, -1, 0, 0)) == (0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def test_a_canonical_pose_is_its_own_canonical_form() -> None:
    # This one moved in its last digit when normalised a second time.
    pose = (77.102949, -103.319525, 476.255106, -0.570518, -0.419718, 0.193257, 0.678967)
    assert canonical_pose(pose) == pose


def test_canonicalising_twice_changes_nothing() -> None:
    rng = random.Random(7)
    for _ in range(5000):
        q = [rng.uniform(-1.0, 1.0) for _ in range(4)]
        length = math.sqrt(math.fsum(v * v for v in q))
        once = canonical_pose(
            [rng.uniform(-500.0, 500.0) for _ in range(3)] + [v / length for v in q]
        )
        flipped = once[:3] + tuple(-v for v in once[3:])
        assert canonical_pose(once) == once
        assert canonical_pose(flipped) == once


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


@pytest.mark.parametrize(
    "pose", [(0, 0, 0, 0, 0, 1), (0, 0, 0, 0, 0, 0, 1, 0), 5, None, "1234567", b"1234567"]
)
def test_a_pose_that_is_not_seven_numbers_is_refused(pose: object) -> None:
    with pytest.raises(ItemKeyError, match="seven"):
        canonical_pose(pose)


@pytest.mark.parametrize(
    "pose",
    [
        (float("nan"), 0, 0, 0, 0, 0, 1),
        (0, 0, 0, 0, 0, 0, float("inf")),
        (True, 0, 0, 0, 0, 0, 1),
        (10**400, 0, 0, 0, 0, 0, 1),
    ],
)
def test_a_pose_holding_a_non_number_is_refused(pose: tuple) -> None:
    with pytest.raises(ItemKeyError, match="finite|too large|int or float"):
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


def test_the_literals_value_types_are_the_ones_the_engine_overrides_with() -> None:
    # A copy, so the module stands alone; this keeps the copy in step.
    assert set(item_key_module._PARAM_VALUE_TYPES) == set(params_module._VALUE_TYPES)


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
        pytest.param("params = {'w': 1, 'w': 2}\n", id="name-twice"),
        pytest.param("base = {}\nparams = {**base}\n", id="unpacks-a-mapping"),
        pytest.param("params = {'w': 1}\nparams |= {'w': 2}\n", id="merged-into"),
        pytest.param("params = {'w': 1}\nfor params in []:\n    pass\n", id="loop-target"),
        pytest.param("params = {'w': 1}\nif (params := {}):\n    pass\n", id="walrus"),
        pytest.param("params = {'w': 1}\ndel params\n", id="deleted"),
        pytest.param(
            "params = {'w': 1}\ndef f():\n    global params\n    params['w'] = 2\n", id="global"
        ),
        pytest.param("params = {'w': 1}\ndef params():\n    pass\n", id="def"),
        pytest.param("params = {'w': 1}\ndef f[params](x):\n    pass\n", id="type-parameter"),
        pytest.param("params = {'w': 1}\ndef f[*params]():\n    pass\n", id="type-tuple"),
        pytest.param("params = {'w': 1}\ndef f[**params]():\n    pass\n", id="param-spec"),
        pytest.param(
            "params = {'w': 1}\nmatch []:\n    case [*params]:\n        pass\n", id="match-star"
        ),
        pytest.param(
            "params = {'w': 1}\nmatch {}:\n    case {**params}:\n        pass\n", id="match-rest"
        ),
        pytest.param("params = {'w': 1}\ndef f(params):\n    return params\n", id="argument"),
        pytest.param("params = {'w': 1}\nimport math as params\n", id="import-as"),
        pytest.param("params = {'w': 1}\nclass params:\n    pass\n", id="class"),
        pytest.param(
            "params = {'w': 1}\ntry:\n    pass\nexcept ValueError as params:\n    pass\n",
            id="except-as",
        ),
        pytest.param(
            "params = {'w': 1}\nmatch 1:\n    case params:\n        pass\n", id="match-capture"
        ),
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
        pytest.param("params = {}\nx = " + "+".join(["1"] * 2_000), id="nested-past-the-limit"),
        pytest.param("params = {}\nx = " + "+".join(["1"] * 100_000), id="too-deep-to-parse"),
        pytest.param("params = {}\nx = f'{y!r:\\x00>3}'\n", id="nul-written-back"),
        pytest.param("params = {}\nx = f'{y!r:\\ud800>3}'\n", id="surrogate-written-back"),
    ],
)
def test_a_recipe_python_cannot_parse_or_write_back_is_refused(source: str) -> None:
    with pytest.raises(ItemKeyError, match="recipe"):
        canonical_recipe(source)


def test_a_recipe_whose_text_reads_back_as_another_program_is_refused() -> None:
    # ast.unparse writes the format spec "}" as "}}", which reads back as an empty
    # spec followed by a literal brace: the spelling that means that is kept.
    with pytest.raises(ItemKeyError, match="read back"):
        canonical_recipe("params = {}\ntag = f'{v:\\x7d}'\n")
    assert canonical_recipe("params = {}\ntag = f'{v:}}}'\n") == b"params = {}\ntag = f'{v:}}}'"


def test_a_refusal_reads_the_same_on_every_call() -> None:
    messages = set()
    for _ in range(2):
        with pytest.raises(ItemKeyError) as refused:
            canonical_recipe("params = {'w': w0}\n")
        messages.add(str(refused.value))
    assert messages == {"params value w0 is not a literal"}


def test_a_recipe_gets_the_same_text_whatever_the_warning_filters() -> None:
    source = "params = {}\npattern = '\\d+'\n"  # an invalid escape: a SyntaxWarning
    plain = canonical_recipe(source)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert canonical_recipe(source) == plain


def _under_each_digit_limit(check: Callable[[], None]) -> None:
    # 640 is the lowest limit a process can set, 0 means none.
    default = sys.get_int_max_str_digits()
    for limit in (640, default, 0):
        sys.set_int_max_str_digits(limit)
        try:
            check()
        finally:
            sys.set_int_max_str_digits(default)


def test_an_integer_literal_past_640_digits_is_refused_whatever_the_limit() -> None:
    source = "params = {}\nx = 0x" + "f" * 600 + "\n"  # 723 digits, written in hex

    def refused() -> None:
        with pytest.raises(ItemKeyError, match="640"):
            canonical_recipe(source)

    _under_each_digit_limit(refused)


def test_an_integer_literal_of_640_digits_gets_one_text_whatever_the_limit() -> None:
    source = "params = {}\nx = " + "9" * 640 + "\n"
    texts = []
    _under_each_digit_limit(lambda: texts.append(canonical_recipe(source)))
    assert len(set(texts)) == 1


def _chain(terms: int) -> str:
    # An expression whose syntax tree nests terms + 2 levels below the module.
    return "params = {}\nx = " + "+".join(["1"] * terms) + "\n"


def test_how_deep_a_recipe_nests_is_refused_at_a_fixed_limit() -> None:
    with pytest.raises(ItemKeyError, match="deeper than 100"):
        canonical_recipe(_chain(99))


def _from_depth(frames: int, call: Callable[[], object]) -> object:
    return _from_depth(frames - 1, call) if frames else call()


def test_a_recipe_within_the_limit_gets_a_key_from_a_deep_caller() -> None:
    assert _from_depth(400, lambda: canonical_recipe(_chain(98))) == canonical_recipe(_chain(98))


# Calls left free under the interpreter's recursion limit, which an import can
# raise: too few for either piece of work below, whatever the limit is.
_HEADROOM = 60


def _frames_left() -> int:
    # How many more calls fit under the interpreter's limit as it stands.
    frame, depth = sys._getframe(), 0
    while frame is not None:
        frame, depth = frame.f_back, depth + 1
    return sys.getrecursionlimit() - depth


def test_a_caller_too_deep_to_write_the_recipe_back_is_refused() -> None:
    with pytest.raises(ItemKeyError, match="recipe"):
        _from_depth(_frames_left() - _HEADROOM, lambda: canonical_recipe(_chain(98)))


def test_a_caller_too_deep_to_write_a_value_is_refused() -> None:
    with pytest.raises(ItemKeyError, match="deep"):
        _from_depth(_frames_left() - _HEADROOM, lambda: jcs(_nested(100)))


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
    # Imported here rather than at the top, so the rest of this file runs without
    # the geometry kernel.
    from cadless.joinery import script_namespace

    assert set(HELPER_SOURCES) == set(script_namespace())


def test_every_listed_helper_source_is_a_file_beside_the_module() -> None:
    package = Path(item_key_module.__file__).parent
    for files in HELPER_SOURCES.values():
        for name in files:
            assert (package / name).is_file(), name


def test_a_helpers_listed_sources_hold_every_engine_module_they_import() -> None:
    # A helper's output can change through any engine module it imports, so each
    # listed file's engine imports must be listed too.
    package = Path(item_key_module.__file__).parent
    for helper, files in HELPER_SOURCES.items():
        listed = {name.removesuffix(".py") for name in files}
        for name in files:
            imported = _engine_imports(ast.parse((package / name).read_bytes()))
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


# --- toolchain identity -----------------------------------------------------


def _parts(toolchain: str) -> dict[str, str]:
    return dict(part.split("=", 1) for part in toolchain.split(";"))


@pytest.fixture
def engine_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # A copy of the files the toolchain reads, so a test can change their bytes.
    package = Path(item_key_module.__file__).parent
    for name in {*RUN_AND_BAKE_FILES, *(f for files in HELPER_SOURCES.values() for f in files)}:
        shutil.copy(package / name, tmp_path / name)
    monkeypatch.setattr(item_key_module, "_PACKAGE_DIR", tmp_path)
    return tmp_path


def _change_one_byte(path: Path) -> None:
    # Same length, other bytes: a digest of anything short of the bytes misses it.
    data = bytearray(path.read_bytes())
    data[0] ^= 1
    path.write_bytes(bytes(data))


def test_the_toolchain_has_these_parts_and_no_engine_version() -> None:
    toolchain = toolchain_identity()
    parts = [part.split("=", 1)[0] for part in toolchain.split(";")]
    assert parts == ["build123d", "ocp", "python", "platform", "runtime", "helpers", "fonts"]
    assert cadless.__version__ not in toolchain


def test_the_toolchain_names_the_kernel_and_interpreter_it_runs_on() -> None:
    parts = _parts(toolchain_identity())
    (ocp,) = set(metadata.packages_distributions()["OCP"])
    assert parts["build123d"] == f"build123d=={metadata.version('build123d')}"
    assert parts["ocp"] == f"{ocp}=={metadata.version(ocp)}"
    assert parts["python"] == f"{sys.version_info.major}.{sys.version_info.minor}"
    assert parts["platform"] == f"{sys.platform}-{platform.machine()}"


@pytest.mark.parametrize(
    "name",
    [
        "script_runtime.py",
        "api_subset.py",
        "params.py",
        "worker.py",
        "_worker_child.py",
        "assembly_tree.py",
    ],
)
def test_a_run_and_bake_file_changes_the_runtime_part_alone(engine_copy: Path, name: str) -> None:
    before = _parts(toolchain_identity())
    _change_one_byte(engine_copy / name)
    after = _parts(toolchain_identity())
    assert after.pop("runtime") != before.pop("runtime")
    assert after == before


def test_an_engine_file_that_is_not_hashed_changes_nothing(engine_copy: Path) -> None:
    before = toolchain_identity({"split_and_join"})
    (engine_copy / "validation.py").write_text("x = 1\n")
    (engine_copy / "__init__.py").write_text('__version__ = "9.9.9"\n')
    assert toolchain_identity({"split_and_join"}) == before


@pytest.mark.parametrize("name", ["joinery.py", "printer_profile.py"])
def test_a_helpers_source_changes_only_the_leaves_that_call_it(
    engine_copy: Path, name: str
) -> None:
    calling, not_calling = toolchain_identity({"split_and_join"}), toolchain_identity()
    _change_one_byte(engine_copy / name)
    assert _parts(toolchain_identity({"split_and_join"}))["helpers"] != _parts(calling)["helpers"]
    assert toolchain_identity() == not_calling


def test_the_helpers_part_is_a_marker_until_a_helper_is_called() -> None:
    assert _parts(toolchain_identity())["helpers"] == "-"
    assert re.fullmatch("[0-9a-f]{64}", _parts(toolchain_identity({"split_and_join"}))["helpers"])


def test_the_fonts_part_is_a_marker() -> None:
    assert _parts(toolchain_identity())["fonts"] == "-"


def test_a_kernel_version_change_changes_the_toolchain(monkeypatch: pytest.MonkeyPatch) -> None:
    before = _parts(toolchain_identity())["build123d"]
    real = metadata.version
    monkeypatch.setattr(
        metadata, "version", lambda name: "0.0.0" if name == "build123d" else real(name)
    )
    assert _parts(toolchain_identity())["build123d"] == "build123d==0.0.0" != before


class _Declaring:
    # A stand-in installed distribution that declares top-level modules.
    def __init__(self, name: str, *modules: str) -> None:
        self.metadata = {"Name": name}
        self._top_level = "".join(f"{module}\n" for module in modules)

    def read_text(self, filename: str) -> str | None:
        return self._top_level if filename == "top_level.txt" else None


def _without_declared(module: str) -> list:
    # The installed distributions, less those that declare ``module``.
    return [
        dist
        for dist in metadata.distributions()
        if module not in (dist.read_text("top_level.txt") or "").split()
    ]


def test_a_kernel_module_no_distribution_provides_is_named(monkeypatch: pytest.MonkeyPatch) -> None:
    others = _without_declared("OCP")
    monkeypatch.setattr(metadata, "distributions", lambda: iter(others))
    monkeypatch.setattr(metadata, "packages_distributions", lambda: {})
    with pytest.raises(ItemKeyError, match="OCP"):
        toolchain_identity()


def test_a_kernel_module_two_distributions_provide_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    both = [*metadata.distributions(), _Declaring("cadquery-ocp", "OCP")]
    monkeypatch.setattr(metadata, "distributions", lambda: iter(both))
    with pytest.raises(ItemKeyError, match="OCP"):
        toolchain_identity()


def test_a_kernel_module_no_distribution_declares_is_found_by_its_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = _parts(toolchain_identity())["ocp"]
    distribution = expected.split("==")[0]
    others = _without_declared("OCP")
    monkeypatch.setattr(metadata, "distributions", lambda: iter(others))
    monkeypatch.setattr(metadata, "packages_distributions", lambda: {"OCP": [distribution]})
    assert _parts(toolchain_identity())["ocp"] == expected


def test_an_unknown_helper_is_refused() -> None:
    with pytest.raises(ItemKeyError, match="helper"):
        toolchain_identity({"no_such_helper"})


@pytest.mark.parametrize("helpers", ["split_and_join", 5, [["split_and_join"]]])
def test_helpers_must_be_a_collection_of_names(helpers: object) -> None:
    with pytest.raises(ItemKeyError, match="collection"):
        toolchain_identity(helpers)


def test_a_hashed_file_that_cannot_be_read_is_refused(engine_copy: Path) -> None:
    (engine_copy / "assembly_tree.py").unlink()
    with pytest.raises(ItemKeyError, match="assembly_tree.py"):
        toolchain_identity()


# Asks for a toolchain that calls split_and_join while both of its source files are
# missing; argv[1] is the source tree, argv[2] a directory holding the other files.
_HELPERS_MISSING = """
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from cadless import item_key

item_key._PACKAGE_DIR = Path(sys.argv[2])
try:
    item_key.toolchain_identity({"split_and_join"})
except item_key.ItemKeyError as refused:
    print(str(refused).split(" beside")[0])
"""


def test_an_unreadable_helper_source_is_named_the_same_under_any_hash_seed(
    tmp_path: Path,
) -> None:
    package = Path(item_key_module.__file__).parent
    for name in RUN_AND_BAKE_FILES:
        shutil.copy(package / name, tmp_path / name)
    named = set()
    for seed in range(8):  # set order differs between some of these seeds
        run = subprocess.run(
            [sys.executable, "-c", _HELPERS_MISSING, str(package.parent), str(tmp_path)],
            env={**os.environ, "PYTHONHASHSEED": str(seed)},
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert run.returncode == 0, run.stderr
        named.add(run.stdout.strip())
    assert named == {"cannot read joinery.py"}


# The engine modules a run-and-bake file imports that are not hashed with it, and
# why none of them can change a built solid.
_NOT_HASHED = {
    "validation": "the code gate: it refuses a recipe or lets it run, never shapes it",
    "joinery": "a helper, hashed into the leaves that call it",
    "exporters": "writes files from the solid; a key names the solid",
    "assembly_measure": "measures the parts it is given",
    "assembly_check": "the record of those measurements",
    "config": "clocks, limits and addresses",
    "print_fit": "reads the sizes a build reports",
}


def test_every_engine_module_a_run_and_bake_file_imports_is_hashed_or_known() -> None:
    package = Path(item_key_module.__file__).parent
    hashed = {name.removesuffix(".py") for name in RUN_AND_BAKE_FILES}
    for name in RUN_AND_BAKE_FILES:
        imported = _engine_imports(ast.parse((package / name).read_bytes()))
        unknown = imported - hashed - set(_NOT_HASHED)
        assert not unknown, f"{name} imports {sorted(unknown)}: hash it, or say why not"


# Imports cadless.item_key through a symbolic link from a package directory whose
# run-and-bake files differ from the ones beside the link's target, then prints the
# toolchain's runtime part. argv[1] is that package's parent directory.
_THROUGH_A_LINK = """
import sys

sys.path.insert(0, sys.argv[1])
from cadless import item_key

assert item_key.__file__.startswith(sys.argv[1]), item_key.__file__
print(dict(p.split("=", 1) for p in item_key.toolchain_identity().split(";"))["runtime"])
"""


def test_the_files_hashed_are_the_ones_beside_the_module_as_imported(tmp_path: Path) -> None:
    package = Path(item_key_module.__file__).parent
    copy = tmp_path / "cadless"
    copy.mkdir()
    (copy / "__init__.py").write_text("")
    for name in RUN_AND_BAKE_FILES:
        shutil.copy(package / name, copy / name)
    _change_one_byte(copy / "params.py")
    (copy / "item_key.py").symlink_to(package / "item_key.py")
    run = subprocess.run(
        [sys.executable, "-I", "-B", "-c", _THROUGH_A_LINK, str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert run.returncode == 0, run.stderr
    assert run.stdout.strip() != _parts(toolchain_identity())["runtime"]


# Puts a stand-in build123d package ahead of the installed one on the path, then
# asks for the toolchain. argv[1] is the source tree, argv[2] the stand-in's parent.
_SHADOWED_KERNEL = """
import sys

sys.path.insert(0, sys.argv[1])
sys.path.insert(0, sys.argv[2])
from cadless import item_key

try:
    item_key.toolchain_identity()
except item_key.ItemKeyError as refused:
    print(refused)
"""


def test_a_kernel_imported_from_files_its_distribution_did_not_install_is_refused(
    tmp_path: Path,
) -> None:
    (tmp_path / "build123d").mkdir()
    (tmp_path / "build123d" / "__init__.py").write_text("")
    root = Path(item_key_module.__file__).parents[1]
    run = subprocess.run(
        [sys.executable, "-I", "-B", "-c", _SHADOWED_KERNEL, str(root), str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert run.returncode == 0, run.stderr
    assert "did not install" in run.stdout


# Imports the module with build123d and OCP unable to run, as a caller without the
# geometry kernel would, and checks that it loaded nothing but the standard
# library beside itself; argv[1] is the source tree to import it from, argv[2] a
# toolchain to key a leaf with. Locating the kernel is allowed: running it is not.
_WITHOUT_KERNEL = """
import sys
from importlib.abc import Loader, MetaPathFinder
from importlib.machinery import PathFinder


class Refuse(Loader):
    def create_module(self, spec):
        raise ImportError("blocked: " + spec.name)

    def exec_module(self, module):
        raise ImportError("blocked: " + module.__name__)


class Blocked(MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name.partition(".")[0] not in ("build123d", "OCP"):
            return None
        spec = PathFinder.find_spec(name, path)
        if spec is not None:
            spec.loader = Refuse()
        return spec


sys.meta_path.insert(0, Blocked())
sys.path.insert(0, sys.argv[1])
before = set(sys.modules)
from cadless import item_key

assert item_key.__file__.startswith(sys.argv[1]), item_key.__file__
print(item_key.toolchain_identity({"split_and_join"}))
print(item_key.leaf_key("params = {'w': 1.0}\\nx = params['w']\\n", {"w": 1}, {"w": "float"}, sys.argv[2]))
loaded = set(sys.modules) - before
outside = {name.partition(".")[0] for name in loaded} - set(sys.stdlib_module_names)
assert outside == {"cadless"}, sorted(outside)
engine = sorted(name for name in loaded if name.startswith("cadless."))
assert engine == ["cadless.item_key"], engine
"""


def test_the_module_works_without_importing_the_kernel() -> None:
    root = Path(item_key_module.__file__).parents[1]
    run = subprocess.run(
        [sys.executable, "-I", "-B", "-c", _WITHOUT_KERNEL, str(root), TOOLCHAIN],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert run.returncode == 0, run.stderr
    toolchain, key = run.stdout.splitlines()
    assert toolchain.startswith("build123d=build123d==")
    source = "params = {'w': 1.0}\nx = params['w']\n"
    assert key == leaf_key(source, {"w": 1}, {"w": "float"}, TOOLCHAIN)


# --- leaf and item keys -----------------------------------------------------

# A toolchain as toolchain_identity writes it, fixed but for the Python it names,
# which must be the one running: keys do not depend on the rest of the machine.
PYTHON = f"{sys.version_info.major}.{sys.version_info.minor}"
TOOLCHAIN = (
    f"build123d=build123d==0.11.1;ocp=cadquery-ocp-novtk==7.9.3.1.1;python={PYTHON};"
    "platform=linux-x86_64;runtime=" + "ab" * 32 + ";helpers=-;fonts=-"
)
TOOLCHAIN_WITH_HELPER = TOOLCHAIN.replace("helpers=-", "helpers=" + "cd" * 32)
PLATE_TYPES = {"width": "float", "height": "float", "label": "str"}
PLATE_PARAMS = {"width": 40, "height": 20.5, "label": "A"}
JOINED = "params = {'x': 50.0}\na, b = split_and_join(part, plane, axis, params['x'])\n"


def test_a_leaf_key_is_the_sha256_of_recipe_params_and_toolchain() -> None:
    preimage = b"\x00".join(
        [
            canonical_recipe(PLATE),
            canonical_params(PLATE_PARAMS, PLATE_TYPES),
            TOOLCHAIN.encode("utf-8"),
        ]
    )
    key = leaf_key(PLATE, PLATE_PARAMS, PLATE_TYPES, TOOLCHAIN)
    assert key == hashlib.sha256(preimage).hexdigest()


def test_a_leaf_key_ignores_what_cannot_change_the_shape() -> None:
    rewritten = "# a comment\n" + PLATE.replace('"""One hole."""', '"""A hole."""')
    assert leaf_key(rewritten, PLATE_PARAMS, PLATE_TYPES, TOOLCHAIN) == leaf_key(
        PLATE, PLATE_PARAMS, PLATE_TYPES, TOOLCHAIN
    )


@pytest.mark.parametrize(
    "change",
    [
        pytest.param({"params": {**PLATE_PARAMS, "width": 41}}, id="a-param-value"),
        pytest.param({"source": PLATE.replace("hole(6)", "hole(7)")}, id="the-recipe"),
        pytest.param({"toolchain": TOOLCHAIN.replace("x86_64", "aarch64")}, id="the-toolchain"),
    ],
)
def test_a_leaf_key_changes_with_each_part(change: dict) -> None:
    base = {"source": PLATE, "params": PLATE_PARAMS, "types": PLATE_TYPES, "toolchain": TOOLCHAIN}
    assert leaf_key(**{**base, **change}) != leaf_key(**base)


def test_every_name_in_the_params_literal_must_be_declared() -> None:
    # An undeclared name would run with the literal's value, which no part of the key holds.
    types = {"width": "float", "height": "float"}
    with pytest.raises(ItemKeyError, match="label"):
        leaf_key(PLATE, {"width": 40, "height": 20.5}, types, TOOLCHAIN)


def test_every_declared_name_must_be_in_the_params_literal() -> None:
    with pytest.raises(ItemKeyError, match="depth"):
        leaf_key(PLATE, {**PLATE_PARAMS, "depth": 3}, {**PLATE_TYPES, "depth": "float"}, TOOLCHAIN)


def test_a_leaf_calling_a_helper_is_keyed_on_the_helpers_digest() -> None:
    other = TOOLCHAIN_WITH_HELPER.replace("cd" * 32, "ef" * 32)
    args = (JOINED, {"x": 50}, {"x": "float"})
    assert leaf_key(*args, TOOLCHAIN_WITH_HELPER) != leaf_key(*args, other)


@pytest.mark.parametrize(
    "body",
    [
        "result = extrude(Text('Hi', params['w']), 2)",
        "result = Compound.make_text('Hi', params['w'])",
        "line = DimensionLine([(0, 0), (params['w'], 0)])",
        "line = ExtensionLine([(0, 0), (params['w'], 0)], offset=5)",
        "page = TechnicalDrawing()",
        "draw = Text\nresult = draw('Hi', params['w'])",
        "from build123d import Text as T\nresult = extrude(T('Hi', params['w']), 2)",
        "import build123d\nresult = extrude(build123d.Text('Hi', params['w']), 2)",
        "import build123d as b\nresult = extrude(b.Text('Hi', params['w']), 2)",
        "result = Box(len(Compound.make_triad(params['w']).edges()), 1, 1)",
        "part = Box(params['w'], 1, 1)\nj = RigidJoint('j', to_part=part)\nlabel = j.symbol",
        "part = Box(params['w'], 1, 1)\nj = BallJoint('j', to_part=part)\nlabel = j.symbol",
    ],
)
def test_a_leaf_that_draws_text_gets_no_key(body: str) -> None:
    # No font ships with the engine for recipes, so the key could not say which drew it.
    source = f"from build123d import *\nparams = {{'w': 5.0}}\n{body}\n"
    with pytest.raises(ItemKeyError, match="text"):
        leaf_key(source, {"w": 5.0}, {"w": "float"}, TOOLCHAIN)


@pytest.mark.parametrize(
    "call",
    [
        pytest.param(lambda: typed_params({"n": 10**5000}, {"n": "bool"}), id="typed-param"),
        pytest.param(lambda: canonical_pose((10**5000, 0, 0, 0, 0, 0, 1)), id="pose"),
        pytest.param(lambda: item_key([10**5000]), id="document"),
        pytest.param(lambda: toolchain_identity(10**5000), id="helpers"),
    ],
)
def test_a_refusal_shows_an_integer_too_long_to_print_without_failing(call) -> None:
    # repr() of an integer past the interpreter's digit limit raises ValueError.
    with pytest.raises(ItemKeyError, match="cannot be shown"):
        call()


class _Unshowable(float):
    def __repr__(self) -> str:
        raise RuntimeError("no")


def test_a_refusal_survives_a_value_whose_repr_raises() -> None:
    with pytest.raises(
        ItemKeyError, match="holds <a value of type _Unshowable that cannot be shown>"
    ):
        typed_params({"flag": _Unshowable(1.0)}, {"flag": "bool"})


def test_every_public_function_is_refused_rather_than_left_too_deep() -> None:
    # Each is wrapped so that a RecursionError from a deep caller becomes a refusal.
    for name in item_key_module.__all__:
        value = getattr(item_key_module, name)
        if callable(value) and not isinstance(value, type):
            assert hasattr(value, "__wrapped__"), name


def test_a_leaf_naming_text_only_inside_a_string_gets_a_key() -> None:
    source = "from build123d import *\nparams = {'w': 5.0}\nlabel = 'Text'\n"
    assert re.fullmatch("[0-9a-f]{64}", leaf_key(source, {"w": 5.0}, {"w": "float"}, TOOLCHAIN))


def test_a_leaf_key_is_computed_on_the_python_its_toolchain_names() -> None:
    # The canonical recipe is ast.unparse of the running interpreter.
    with pytest.raises(ItemKeyError, match="Python"):
        leaf_key(PLATE, PLATE_PARAMS, PLATE_TYPES, TOOLCHAIN.replace(PYTHON, "2.7"))


def test_the_toolchain_must_hold_the_helpers_a_recipe_calls() -> None:
    with pytest.raises(ItemKeyError, match="helper"):
        leaf_key(JOINED, {"x": 50}, {"x": "float"}, TOOLCHAIN)


def test_the_toolchain_must_hold_no_helpers_a_recipe_does_not_call() -> None:
    # One leaf, one key: a toolchain computed for another recipe is refused.
    with pytest.raises(ItemKeyError, match="helper"):
        leaf_key(PLATE, PLATE_PARAMS, PLATE_TYPES, TOOLCHAIN_WITH_HELPER)


@pytest.mark.parametrize(
    "toolchain",
    [
        pytest.param(TOOLCHAIN.encode(), id="bytes"),
        pytest.param("build123d=build123d==0.11.1;fonts=-", id="no-helpers-part"),
        pytest.param(TOOLCHAIN + "\ud800", id="lone-surrogate"),
        pytest.param(TOOLCHAIN + "\x00", id="nul"),
    ],
)
def test_a_toolchain_that_is_not_toolchain_text_is_refused(toolchain: object) -> None:
    with pytest.raises(ItemKeyError, match="toolchain"):
        leaf_key(PLATE, PLATE_PARAMS, PLATE_TYPES, toolchain)


MEMBERS = {
    "members": [
        {"key": "1" * 64, "pose": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0]},
        {"key": "2" * 64, "pose": [10.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0]},
    ]
}


def test_an_item_key_is_the_sha256_of_its_canonical_document() -> None:
    assert item_key(MEMBERS) == hashlib.sha256(jcs(MEMBERS)).hexdigest()


def test_an_item_key_leaves_out_the_documents_own_key() -> None:
    assert item_key({**MEMBERS, "key": "f" * 64}) == item_key(MEMBERS)


def test_an_item_key_changes_with_any_members_key() -> None:
    first, second = MEMBERS["members"]
    assert item_key({"members": [first, {**second, "key": "3" * 64}]}) != item_key(MEMBERS)


@pytest.mark.parametrize("document", [[1, 2], "members", None])
def test_a_document_that_is_not_a_mapping_is_refused(document: object) -> None:
    with pytest.raises(ItemKeyError, match="document"):
        item_key(document)


# --- shared vectors ---------------------------------------------------------

VECTORS = json.loads(
    Path(item_key_module.__file__).with_name("item_key_vectors.json").read_text(encoding="utf-8")
)

# Each section, and the field that holds a case's expected outcome beside "refused".
_OUTCOME = {
    "jcs": "output",
    "params": "output",
    "pose": "output",
    "recipe": "output",
    "leaf": "key",
    "item": "key",
}


def _vectors(section: str) -> list:
    return [pytest.param(case, id=case["name"]) for case in VECTORS[section]]


def _expect(case: dict, field: str, compute: Callable[[], object]) -> None:
    if case.get("refused"):
        with pytest.raises(ItemKeyError):
            compute()
    else:
        assert compute() == case[field]


def test_the_vectors_are_for_these_definitions() -> None:
    assert VECTORS["definitions"] == DEFINITIONS_VERSION


def test_every_vector_names_one_outcome() -> None:
    assert set(VECTORS) == {"about", "definitions", *_OUTCOME}
    for section, field in _OUTCOME.items():
        names = [case["name"] for case in VECTORS[section]]
        assert len(names) == len(set(names)), section
        for case in VECTORS[section]:
            assert (field in case) != bool(case.get("refused")), case["name"]


@pytest.mark.parametrize("case", _vectors("jcs"))
def test_jcs_vectors(case: dict) -> None:
    value = _double(case["bits"]) if "bits" in case else case["input"]
    _expect(case, "output", lambda: jcs(value).decode("utf-8"))


@pytest.mark.parametrize("case", _vectors("params"))
def test_params_vectors(case: dict) -> None:
    _expect(case, "output", lambda: canonical_params(case["params"], case["types"]).decode())


@pytest.mark.parametrize("case", _vectors("pose"))
def test_pose_vectors(case: dict) -> None:
    _expect(case, "output", lambda: jcs(canonical_pose(case["pose"])).decode("utf-8"))


@pytest.mark.parametrize("case", _vectors("recipe"))
def test_recipe_vectors(case: dict) -> None:
    _expect(case, "output", lambda: canonical_recipe(case["source"]).decode("utf-8"))
    if "helpers" in case:
        assert sorted(helpers_called(case["source"])) == case["helpers"]


def test_the_leaf_vectors_cover_this_python() -> None:
    # A leaf case naming a Python applies on that Python only: a new interpreter
    # needs its own cases before its keys are pinned.
    assert any(case.get("python") == PYTHON for case in VECTORS["leaf"])


@pytest.mark.parametrize("case", _vectors("leaf"))
def test_leaf_vectors(case: dict) -> None:
    if case.get("python", PYTHON) != PYTHON:
        pytest.skip(f"a case for Python {case['python']}")
    args = (case["source"], case["params"], case["types"], case["toolchain"])
    _expect(case, "key", lambda: leaf_key(*args))


@pytest.mark.parametrize("case", _vectors("item"))
def test_item_vectors(case: dict) -> None:
    _expect(case, "key", lambda: item_key(case["document"]))
