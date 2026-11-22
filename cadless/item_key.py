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

import ast
import hashlib
import math
import platform
import sys
from collections.abc import Iterable, Mapping, Sequence
from importlib import metadata
from pathlib import Path
from types import MappingProxyType

__all__ = [
    "HELPER_SOURCES",
    "PARAM_TYPES",
    "RUN_AND_BAKE_FILES",
    "ItemKeyError",
    "canonical_params",
    "canonical_pose",
    "canonical_recipe",
    "helpers_called",
    "jcs",
    "toolchain_identity",
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


# The module-level name a recipe's parameter literal is bound to, and the types
# of value the literal may hold.
_PARAMS_NAME = "params"
_PARAM_VALUE_TYPES = (bool, int, float, str)

# The nodes whose first statement, when it is a string, is a docstring.
_DOCUMENTED = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)

#: Each engine helper a recipe is handed, by name, and the files beside this
#: module whose bytes a leaf calling that helper is keyed on: the helper's module
#: and every engine module it imports.
HELPER_SOURCES: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {"split_and_join": ("joinery.py", "printer_profile.py")}
)


def canonical_recipe(source: str) -> bytes:
    """``source`` as the text a leaf key covers: what it does, short of its params values.

    The module is parsed and written back with ``ast.unparse``, so comments and
    layout drop out, and every docstring is dropped (a body it leaves empty keeps a
    ``pass``). The one module-level ``params = {...}`` literal stays where it
    stands, its names in their order and each value written as ``...``: the values
    are keyed by ``canonical_params``, but where the literal stands and the order of
    its names can change what the recipe does. Encoded as UTF-8.

    Raises ``ItemKeyError`` unless ``source`` is text that Python can parse and
    write back, binding ``params`` at module level exactly once, as a plain
    (unannotated) dict literal from string names to numbers, strings or booleans.
    """
    text, _names = _canonical_recipe(source)
    return text


def helpers_called(source: str) -> frozenset[str]:
    """The engine helpers in ``HELPER_SOURCES`` that ``source`` loads by name.

    Every load of a helper's bare name counts, even where the recipe binds that
    name itself: telling a recipe's own function from the engine's is not worth
    the risk of leaving the helper's source out of the key.

    Raises ``ItemKeyError`` for a source that Python cannot parse.
    """
    tree = _parse_recipe(source)
    return frozenset(
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name)
        and isinstance(node.ctx, ast.Load)
        and node.id in HELPER_SOURCES
    )


def _canonical_recipe(source: object) -> tuple[bytes, tuple[str, ...]]:
    # The canonical text, and the names of the params literal in their order.
    tree = _parse_recipe(source)
    _drop_docstrings(tree)
    names = _blank_params_literal(tree)
    try:
        text = ast.unparse(tree)
    except RecursionError as error:
        raise ItemKeyError("the recipe is nested too deeply to write back") from error
    return text.encode("utf-8"), names


def _parse_recipe(source: object) -> ast.Module:
    if not isinstance(source, str):
        raise ItemKeyError(f"a recipe is text, not a {type(source).__name__}")
    try:
        return ast.parse(source)
    except (SyntaxError, ValueError, RecursionError) as error:
        # A null byte raises SyntaxError, a lone surrogate UnicodeEncodeError (a
        # ValueError), and nesting past the interpreter's limit RecursionError.
        raise ItemKeyError(f"cannot parse the recipe: {error}") from error


def _drop_docstrings(tree: ast.Module) -> None:
    for node in ast.walk(tree):
        if not isinstance(node, _DOCUMENTED) or not node.body:
            continue
        first = node.body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            del node.body[0]
            if not node.body and not isinstance(node, ast.Module):
                # ast.unparse reads a statement's line number.
                node.body.append(ast.copy_location(ast.Pass(), first))


def _blank_params_literal(tree: ast.Module) -> tuple[str, ...]:
    # Writes the params literal's values as ..., in place; returns its names.
    bindings = []
    for index, node in enumerate(tree.body):
        if isinstance(node, ast.AnnAssign) and _binds_params(node.target):
            raise ItemKeyError("the recipe annotates params; write it as a plain params = {...}")
        if isinstance(node, ast.Assign) and any(_binds_params(t) for t in node.targets):
            bindings.append(index)
    if len(bindings) != 1:
        raise ItemKeyError(
            "a recipe binds params once at module level, as one params = {...} literal; "
            f"this one binds it {len(bindings)} times"
        )
    node = tree.body[bindings[0]]
    if not (
        len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and isinstance(node.value, ast.Dict)
    ):
        raise ItemKeyError("params is bound as params = {...}, a plain dict literal")
    try:
        literal = ast.literal_eval(node.value)
    except (ValueError, TypeError) as error:  # not a literal; an unhashable name
        raise ItemKeyError(
            f"the params literal holds something that is not a literal: {error}"
        ) from error
    for name, default in literal.items():
        if not isinstance(name, str):
            raise ItemKeyError(f"params name {name!r} is not a string")
        if not isinstance(default, _PARAM_VALUE_TYPES):
            raise ItemKeyError(
                f"params value {default!r} of {name!r} is not a number, string or bool"
            )
    names = tuple(literal)
    blank = ast.Assign(
        targets=[ast.Name(id=_PARAMS_NAME, ctx=ast.Store())],
        value=ast.Dict(
            keys=[ast.Constant(name) for name in names],
            values=[ast.Constant(...) for _ in names],
        ),
    )
    tree.body[bindings[0]] = ast.copy_location(blank, node)
    return names


def _binds_params(target: ast.expr) -> bool:
    return any(
        isinstance(node, ast.Name) and node.id == _PARAMS_NAME and isinstance(node.ctx, ast.Store)
        for node in ast.walk(target)
    )


#: The engine files beside this module that run a recipe and bake its solid. A
#: leaf is keyed on their whole bytes rather than on chosen functions in them, so
#: no change to them can slip past the key.
RUN_AND_BAKE_FILES = ("script_runtime.py", "api_subset.py", "_worker_child.py", "assembly_tree.py")

# The geometry kernel, by the toolchain part and the top-level module each is.
_KERNEL = (("build123d", "build123d"), ("ocp", "OCP"))

# Where the hashed files are read from; a test points it at a copy.
_PACKAGE_DIR = Path(__file__).resolve().parent


def toolchain_identity(helpers: Iterable[str] = ()) -> str:
    """What builds a leaf on this machine, as the text a leaf key covers.

    ``build123d=<distribution>==<version>;ocp=<distribution>==<version>;
    python=<major>.<minor>;platform=<sys.platform>-<machine>;runtime=<sha256>;
    helpers=<sha256 or ->;fonts=-``

    Each kernel part names the installed distribution that provides the module and
    its version, read from package metadata without importing the kernel.
    ``runtime`` is the sha256 of the canonical JSON that maps each of
    ``RUN_AND_BAKE_FILES`` to the sha256 of its bytes; ``helpers`` is the same over
    the files ``HELPER_SOURCES`` lists for ``helpers`` (the names ``helpers_called``
    returns), or ``-`` when there are none. No fonts ship for recipes, so ``fonts``
    is ``-``. The engine's version number is left out: a change that can alter a
    shape changes a hashed file or a kernel version instead, and a release that
    changes neither keeps every key.

    Raises ``ItemKeyError`` for a helper ``HELPER_SOURCES`` does not list, a kernel
    module that no installed distribution provides or that more than one does, or a
    hashed file that cannot be read.
    """
    names = sorted(set(helpers))
    unknown = [name for name in names if name not in HELPER_SOURCES]
    if unknown:
        raise ItemKeyError(f"no engine helper is named {unknown[0]!r}")
    parts = _kernel_parts()
    parts.append(f"python={sys.version_info.major}.{sys.version_info.minor}")
    parts.append(f"platform={sys.platform}-{platform.machine()}")
    parts.append(f"runtime={_files_digest(RUN_AND_BAKE_FILES)}")
    helper_files = {file for name in names for file in HELPER_SOURCES[name]}
    parts.append(f"helpers={_files_digest(helper_files) if helper_files else '-'}")
    parts.append("fonts=-")
    return ";".join(parts)


def _kernel_parts() -> list[str]:
    # Each kernel part as part=name==version of the one distribution providing it.
    # The distributions that declare a module in top_level.txt are quick to find;
    # only a module none declares falls back to reading every distribution's file
    # list, which takes far longer.
    declared: dict[str, set[str]] = {module: set() for _, module in _KERNEL}
    for dist in metadata.distributions():
        tops = (dist.read_text("top_level.txt") or "").split()
        for module in declared.keys() & set(tops):
            declared[module].add(dist.metadata["Name"])
    inferred: Mapping[str, list[str]] | None = None
    parts = []
    for part, module in _KERNEL:
        names = declared[module]
        if not names:
            inferred = metadata.packages_distributions() if inferred is None else inferred
            names = set(inferred.get(module, ()))
        if len(names) != 1:
            found = ", ".join(sorted(names)) or "none"
            raise ItemKeyError(
                f"one installed distribution must provide the {module} module; found {found}"
            )
        (name,) = names
        parts.append(f"{part}={name}=={metadata.version(name)}")
    return parts


def _files_digest(names: Iterable[str]) -> str:
    # The sha256 of the canonical JSON mapping each file name to its bytes' sha256.
    digests = {}
    for name in names:
        try:
            data = (_PACKAGE_DIR / name).read_bytes()
        except OSError as error:
            raise ItemKeyError(f"cannot read {name} beside the item key module: {error}") from error
        digests[name] = hashlib.sha256(data).hexdigest()
    return hashlib.sha256(jcs(digests)).hexdigest()


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
