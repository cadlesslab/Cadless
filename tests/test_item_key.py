"""The item key library: canonical forms and the keys built from them."""

from __future__ import annotations

import struct

import pytest

from cadless.item_key import ItemKeyError, jcs


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
