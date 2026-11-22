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
from collections.abc import Mapping, Sequence

__all__ = [
    "PARAM_TYPES",
    "ItemKeyError",
    "canonical_params",
    "canonical_pose",
    "jcs",
    "typed_params",
]


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


PARAM_TYPES = ("float", "int", "str", "bool")

# Poses are rounded to this many decimal places (a millionth of a millimetre for a
# translation); a quaternion further than this from unit length is refused.
_POSE_DECIMALS = 6
_UNIT_TOLERANCE = 1e-6


def typed_params(params: Mapping[str, object], types: Mapping[str, str]) -> dict[str, object]:
    """``params`` converted to their declared ``types``: the values a recipe runs with.

    Every parameter must be declared, and every declaration must have a value.
    An int becomes a float where a float is declared, and an integral float an int
    where an int is; a bool is never taken for a number, nor a number for a bool.
    Strings are kept as given, not normalised.

    Raises ``ItemKeyError`` for a missing or undeclared name, an unknown type, or
    a value that does not fit its type.
    """
    for name in params:
        if name not in types:
            raise ItemKeyError(f"parameter {name!r} has no declared type")
    typed = {}
    for name, declared in types.items():
        if declared not in PARAM_TYPES:
            raise ItemKeyError(
                f"parameter {name!r} declares type {declared!r}, not one of {PARAM_TYPES}"
            )
        if name not in params:
            raise ItemKeyError(f"parameter {name!r} is declared but has no value")
        typed[name] = _typed(name, params[name], declared)
    return typed


def canonical_params(params: Mapping[str, object], types: Mapping[str, str]) -> bytes:
    """``params`` under their declared ``types``, as canonical JSON.

    The values are those ``typed_params`` returns, each written beside its declared
    type, since canonical JSON writes 4 and 4.0 alike. Raises as ``typed_params``.
    """
    typed = typed_params(params, types)
    return jcs({name: {"type": types[name], "value": value} for name, value in typed.items()})


def _typed(name: str, value: object, declared: str) -> object:
    if declared == "bool":
        if not isinstance(value, bool):
            raise ItemKeyError(f"parameter {name!r} is declared bool but holds {value!r}")
        return value
    if declared == "str":
        if not isinstance(value, str):
            raise ItemKeyError(f"parameter {name!r} is declared str but holds {value!r}")
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ItemKeyError(
            f"parameter {name!r} is declared {declared} but holds a bool or non-number {value!r}"
        )
    if not math.isfinite(value):
        raise ItemKeyError(f"parameter {name!r} holds {value!r}, which is not a finite number")
    if declared == "float":
        return float(value)
    if isinstance(value, float):
        if not value.is_integer():
            raise ItemKeyError(
                f"parameter {name!r} is declared int but holds {value!r}, which is not an integer"
            )
        return int(value)
    return value


def canonical_pose(pose: Sequence[float]) -> tuple[float, ...]:
    """``pose`` (x, y, z, qx, qy, qz, qw) in its one canonical form.

    The quaternion is normalised, then every number is rounded to six decimal
    places; the sign is fixed on the rounded quaternion (qw positive, or the first
    non-zero of qx, qy, qz when qw rounds to zero), so q and -q give one pose
    however close qw lies to zero. Negative zero is written as zero.

    Raises ``ItemKeyError`` unless the pose is seven finite numbers whose quaternion
    lies within 1e-6 of unit length.
    """
    if isinstance(pose, (str, bytes)) or len(pose) != 7:
        raise ItemKeyError(f"a pose is seven numbers (x, y, z, qx, qy, qz, qw), not {pose!r}")
    for number in pose:
        if (
            isinstance(number, bool)
            or not isinstance(number, (int, float))
            or not math.isfinite(number)
        ):
            raise ItemKeyError(f"pose {pose!r} holds {number!r}, which is not a finite number")
    translation = [float(v) for v in pose[:3]]
    quaternion = [float(v) for v in pose[3:]]
    length = math.sqrt(sum(v * v for v in quaternion))
    if abs(length - 1.0) > _UNIT_TOLERANCE:
        raise ItemKeyError(f"pose {pose!r} has a quaternion of length {length!r}, not unit length")
    quaternion = [_rounded(v / length) for v in quaternion]
    translation = [_rounded(v) for v in translation]
    qx, qy, qz, qw = quaternion
    leading = qw if qw != 0 else next((v for v in (qx, qy, qz) if v != 0), 0.0)
    if leading < 0:
        quaternion = [-v + 0.0 for v in quaternion]
    return tuple(translation + quaternion)


def _rounded(value: float) -> float:
    # Adding 0.0 turns a negative zero, which round() keeps, into zero.
    return round(value, _POSE_DECIMALS) + 0.0


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
