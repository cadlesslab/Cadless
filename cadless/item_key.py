"""Item keys: one name for a part or an assembly, computed from what it is.

A leaf (one solid built by a recipe) and an item with members are each named by
a key computed from their content. The engine and every caller that builds keys
call this module rather than their own copy, so the same input gives the same key
everywhere; the rules are written down in docs/extending/item-keys.md and pinned by
the vectors shipped beside this file.

The module imports nothing but the standard library. A caller that has no geometry
kernel installed can still compute and check keys, and the module can be copied
into another codebase without dragging the engine along.

Canonical JSON follows RFC 8785 (the JSON Canonicalization Scheme) and is written
here rather than taken from a dependency: the scheme is small, and a key that
changed with a third-party library's release would defeat the point of a key.
"""

from __future__ import annotations

import math
from collections.abc import Mapping

__all__ = ["ItemKeyError", "jcs"]


class ItemKeyError(ValueError):
    """An input that has no canonical form under the item key definitions."""


# JSON numbers are IEEE 754 doubles: an integer past this magnitude cannot be
# written so that every reader gets the same integer back.
_SAFE_INTEGER = 2**53 - 1

_ESCAPES = {
    '"': '\\"',
    "\\": "\\\\",
    "\b": "\\b",
    "\f": "\\f",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}


def jcs(value: object) -> bytes:
    """``value`` as RFC 8785 canonical JSON, encoded as UTF-8.

    Accepts mappings with string keys, lists and tuples, strings, integers,
    floats, booleans and ``None``. Object members are sorted by the UTF-16 code
    units of their keys, numbers take their shortest ECMAScript form, and nothing
    is written between tokens.

    Raises ``ItemKeyError`` for a non-finite number, an integer past +/-(2**53 - 1),
    a non-string key, a string holding a lone surrogate, or any other type.
    """
    return _write(value).encode("utf-8")


def _write(value: object) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        if abs(value) > _SAFE_INTEGER:
            raise ItemKeyError(
                f"integer {value} is past +/-(2**53 - 1), which a JSON number cannot hold exactly"
            )
        return str(value)
    if isinstance(value, float):
        return _number(value)
    if isinstance(value, str):
        return _string(value)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_write(item) for item in value) + "]"
    if isinstance(value, Mapping):
        return _object(value)
    raise ItemKeyError(f"cannot write a {type(value).__name__} as canonical JSON")


def _object(value: Mapping) -> str:
    for key in value:
        if not isinstance(key, str):
            raise ItemKeyError(f"object key {key!r} is not a string")
    members = sorted(value, key=_code_units)
    return "{" + ",".join(_string(key) + ":" + _write(value[key]) for key in members) + "}"


def _code_units(key: str) -> bytes:
    # Big-endian UTF-16 bytes compare in the order of their code units, which is
    # the order RFC 8785 sorts by; it differs from code point order past U+FFFF.
    _string(key)
    return key.encode("utf-16-be")


def _string(text: str) -> str:
    out = []
    for char in text:
        if char in _ESCAPES:
            out.append(_ESCAPES[char])
        elif char < " ":
            out.append(f"\\u{ord(char):04x}")
        elif "\ud800" <= char <= "\udfff":
            raise ItemKeyError(f"string {text!r} holds a lone surrogate, which has no UTF-8 form")
        else:
            out.append(char)
    return '"' + "".join(out) + '"'


def _number(value: float) -> str:
    # ECMAScript's Number-to-String: the shortest digits that read back as the
    # same double, placed by the exponent n that ECMA-262 defines.
    if not math.isfinite(value):
        raise ItemKeyError(f"{value!r} is not a finite number")
    if value == 0:
        return "0"
    if value < 0:
        return "-" + _number(-value)
    digits, n = _shortest_digits(value)
    k = len(digits)
    if k <= n <= 21:
        return digits + "0" * (n - k)
    if 0 < n <= 21:
        return digits[:n] + "." + digits[n:]
    if -6 < n <= 0:
        return "0." + "0" * -n + digits
    exponent = n - 1
    mantissa = digits[0] + ("." + digits[1:] if k > 1 else "")
    return f"{mantissa}e{'+' if exponent >= 0 else '-'}{abs(exponent)}"


def _shortest_digits(value: float) -> tuple[str, int]:
    # repr gives the shortest decimal that round-trips, the same digits ECMAScript
    # picks. Returns them without leading or trailing zeros, and n such that the
    # value is 0.<digits> times 10**n.
    mantissa, _, exponent = repr(value).partition("e")
    whole, _, fraction = mantissa.partition(".")
    digits = whole + fraction
    n = len(whole) + (int(exponent) if exponent else 0)
    significant = digits.lstrip("0")
    n -= len(digits) - len(significant)
    return significant.rstrip("0"), n
