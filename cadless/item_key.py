"""Item keys: one name for a part or an assembly, computed from what it is.

A leaf (one solid built by a recipe) and an item with members are each named by
a key computed from their content. Whatever builds or checks keys is meant to call
this module rather than a copy of its rules, so the same input gives the same key
everywhere; the vectors shipped beside this file pin the rules.

The module imports nothing but the standard library. A caller that has no geometry
kernel installed can still compute and check keys, and the module can be copied
into another codebase as it is; only toolchain_identity needs more, the engine
files beside it and the kernel's installed distributions.

Canonical JSON follows RFC 8785 (the JSON Canonicalization Scheme) and is written
here rather than taken from a dependency: the scheme is small, and a key that
changed with a third-party library's release would defeat the point of a key.
"""

from __future__ import annotations

import ast
import functools
import hashlib
import importlib.util
import math
import platform
import sys
import warnings
from collections.abc import Iterable, Mapping, Sequence
from importlib import metadata
from pathlib import Path
from types import MappingProxyType

__all__ = [
    "DEFINITIONS_VERSION",
    "HELPER_SOURCES",
    "PARAM_TYPES",
    "RUN_AND_BAKE_FILES",
    "ItemKeyError",
    "canonical_params",
    "canonical_pose",
    "canonical_recipe",
    "helpers_called",
    "item_key",
    "jcs",
    "leaf_key",
    "toolchain_identity",
    "typed_params",
]


class ItemKeyError(ValueError):
    """An input that has no canonical form under the item key definitions."""


def _refused_when_too_deep(function):
    # A caller too deep in its own calls for the work is refused like any other
    # input, rather than left with the interpreter's RecursionError. Only a caller
    # at the very limit, with no frame left to enter this one, still gets that.
    @functools.wraps(function)
    def refusing(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except RecursionError as error:
            raise ItemKeyError(f"called too deep to finish: {error}") from error

    return refusing


def _shown(value: object) -> str:
    # A caller's value as a refusal shows it. repr() of an integer longer than the
    # interpreter will write raises, and so may a caller's own __repr__; whatever it
    # raises, the value is described instead, so the refusal stays an ItemKeyError.
    try:
        text = repr(value)
    except Exception:
        return f"<a value of type {type(value).__name__} that cannot be shown>"
    return text if len(text) <= 120 else text[:117] + "..."


#: The version of the definitions this module implements, which the vector file
#: beside it records too. A change to any canonical form or key ships as a new
#: version with new vectors.
DEFINITIONS_VERSION = 1


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


@_refused_when_too_deep
def typed_params(params: Mapping[str, object], types: Mapping[str, str]) -> dict[str, object]:
    """``params`` converted to their declared ``types``: the values a recipe runs with.

    Every parameter must be declared, and every declaration must have a value.
    An int becomes a float where a float is declared, and an integral float an int
    where an int is; a bool is never taken for a number, nor a number for a bool.
    A float negative zero becomes zero, since canonical JSON writes both as 0 and a
    recipe must run with the value its key holds. Each value comes back as a plain
    ``float``, ``int``, ``str`` or ``bool``, whatever subclass it arrived as.
    Strings are kept as given, not normalised.

    Raises ``ItemKeyError`` unless both arguments are mappings, for a missing or
    undeclared name, an unknown type, a value that does not fit its type, or a name
    or value canonical JSON cannot write (so every value returned can be keyed).
    """
    if not isinstance(params, Mapping) or not isinstance(types, Mapping):
        raise ItemKeyError("params and types are each a mapping from parameter names")
    for name in params:
        if name not in types:
            raise ItemKeyError(f"parameter {_shown(name)} has no declared type")
    typed = {}
    for name, declared in types.items():
        if declared not in PARAM_TYPES:
            raise ItemKeyError(
                f"parameter {_shown(name)} declares type {_shown(declared)}, not one of {PARAM_TYPES}"
            )
        if name not in params:
            raise ItemKeyError(f"parameter {_shown(name)} is declared but has no value")
        typed[name] = _typed(name, params[name], declared)
    jcs(typed)  # a non-string name, an integer no double holds, a lone surrogate
    return typed


@_refused_when_too_deep
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
            raise ItemKeyError(
                f"parameter {_shown(name)} is declared bool but holds {_shown(value)}"
            )
        return value
    if declared == "str":
        if not isinstance(value, str):
            raise ItemKeyError(
                f"parameter {_shown(name)} is declared str but holds {_shown(value)}"
            )
        return str.__str__(value)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ItemKeyError(
            f"parameter {_shown(name)} is declared {declared} but holds a bool or non-number {_shown(value)}"
        )
    number = _plain_number(value)
    if isinstance(number, float) and not math.isfinite(number):
        raise ItemKeyError(
            f"parameter {_shown(name)} holds {_shown(value)}, which is not a finite number"
        )
    if declared == "float":
        if isinstance(number, int):
            try:
                number = float(number)
            except OverflowError as error:
                raise ItemKeyError(
                    f"parameter {_shown(name)} holds {_shown(value)}, too large for a float"
                ) from error
        return number + 0.0
    if isinstance(number, float):
        if not number.is_integer():
            raise ItemKeyError(
                f"parameter {_shown(name)} is declared int but holds {_shown(value)}, which is not an integer"
            )
        return int(number)
    return number


def _plain_number(value: int | float) -> int | float:
    # The plain int or float a subclass instance (an IntEnum member, a numpy float)
    # stands for, read through the base type so no override of its own is consulted.
    return float.__float__(value) if isinstance(value, float) else int.__int__(value)


@_refused_when_too_deep
def canonical_pose(pose: Sequence[float]) -> tuple[float, ...]:
    """``pose`` (x, y, z, qx, qy, qz, qw) in its one canonical form.

    The quaternion is normalised, then every number is rounded to six decimal
    places, half to even; the sign is fixed on the rounded quaternion (qw positive,
    or the first non-zero of qx, qy, qz when qw rounds to zero), so q and -q give
    one pose however close qw lies to zero. Negative zero is written as zero.

    A quaternion already on the six-decimal grid and within 2e-6 of unit length (as
    near as rounding a unit quaternion leaves it) is kept as it is, apart from the
    sign: normalising it again could move its last digit, and a canonical form must
    not change when it is canonicalised again.

    Raises ``ItemKeyError`` unless the pose is a sequence of seven finite numbers
    whose quaternion lies within 1e-6 of unit length (or is kept as above).
    """
    if not isinstance(pose, Sequence) or isinstance(pose, (str, bytes)) or len(pose) != 7:
        raise ItemKeyError(f"a pose is seven numbers (x, y, z, qx, qy, qz, qw), not {_shown(pose)}")
    numbers = []
    for number in pose:
        if isinstance(number, bool) or not isinstance(number, (int, float)):
            raise ItemKeyError(
                f"pose {_shown(pose)} holds {_shown(number)}, which is not an int or float"
            )
        try:
            value = float(_plain_number(number))
        except OverflowError as error:
            raise ItemKeyError(
                f"pose {_shown(pose)} holds {_shown(number)}, too large for a float"
            ) from error
        if not math.isfinite(value):
            raise ItemKeyError(
                f"pose {_shown(pose)} holds {_shown(number)}, which is not a finite number"
            )
        numbers.append(value)
    translation, quaternion = numbers[:3], numbers[3:]
    # fsum is exactly rounded, so the length is the same on every interpreter.
    length = math.sqrt(math.fsum(v * v for v in quaternion))
    on_grid = all(v == round(v, _POSE_DECIMALS) for v in quaternion)
    if on_grid and abs(length - 1.0) <= 2 * _UNIT_TOLERANCE:
        quaternion = [v + 0.0 for v in quaternion]
    elif abs(length - 1.0) > _UNIT_TOLERANCE:
        raise ItemKeyError(
            f"pose {_shown(pose)} has a quaternion of length {length!r}, not unit length"
        )
    else:
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

# How deep a recipe's syntax tree, or a value given to jcs, may nest: far beyond
# what a recipe or an item document needs, and far enough below the interpreter's
# recursion limit that a caller a few hundred calls deep gets the same answer as
# one at the top. A caller deeper still meets that limit, and is refused.
_NESTING_LIMIT = 100

# An integer literal past 640 digits is refused: 640 is the lowest limit a process
# can set on writing an integer in decimal, so below it the interpreter writes the
# literal whatever the setting, and the key cannot depend on it.
_HUGE_INTEGER = 10**640

# The build123d names and attributes that draw text. Text needs a font, and no
# font ships with the engine for recipes, so a leaf that draws text has nothing in
# its key to say which font built it. make_triad and a joint's symbol draw their
# letters in a font build123d bundles, which its version in the toolchain fixes,
# but they are refused all the same, so that the rule is that a leaf draws none.
_DRAWS_TEXT = frozenset({"Text", "DimensionLine", "ExtensionLine", "TechnicalDrawing"})
_DRAWS_TEXT_ATTRIBUTES = frozenset({"make_text", "make_triad", "symbol"})

#: Each engine helper a recipe is handed, by name, and the files beside this
#: module whose bytes a leaf calling that helper is keyed on: the helper's module
#: and every engine module it imports.
HELPER_SOURCES: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {"split_and_join": ("joinery.py", "printer_profile.py")}
)


@_refused_when_too_deep
def canonical_recipe(source: str) -> bytes:
    """``source`` as the text a leaf key covers: what it does, short of its params values.

    The module is parsed and written back with ``ast.unparse``, so comments and
    layout drop out, and every docstring is dropped (a body it leaves empty keeps a
    ``pass``). The one module-level ``params = {...}`` literal stays where it
    stands, its names in their order and each value written as ``...``: the values
    are keyed by ``canonical_params``, but where the literal stands and the order of
    its names can change what the recipe does. Encoded as UTF-8.

    Raises ``ItemKeyError`` unless ``source`` is text that Python can parse, and
    that binds ``params`` exactly once and nowhere else: in one plain (unannotated)
    module-level dict literal from distinct string names to numbers, strings or
    booleans. Also refused: syntax nested deeper than 100 levels, an integer literal
    of more than 640 digits, and a recipe whose canonical text would not read back
    as the same program (``ast.unparse`` writes a few constructs, such as a brace or
    a 0x00 byte in a format spec, so that they read back as others or not at all).
    """
    text, _names = _canonical_recipe(source)
    return text


@_refused_when_too_deep
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
    _check_shape(tree)
    _drop_docstrings(tree)
    names = _blank_params_literal(tree)
    try:
        text = ast.unparse(tree)
    except (RecursionError, ValueError) as error:
        raise ItemKeyError(f"cannot write the recipe back: {error}") from error
    # Two recipes could otherwise share a text while doing different things. This
    # also keeps 0x00 out of the text, which ast.unparse writes as it is inside a
    # format spec and no parse reads back: a leaf key's parts cannot run together.
    try:
        same = ast.dump(_parse_recipe(text)) == ast.dump(tree)
    except ItemKeyError:
        same = False
    if not same:
        raise ItemKeyError("the recipe's canonical text does not read back as the same program")
    return text.encode("utf-8"), names


def _parse_recipe(source: object) -> ast.Module:
    if not isinstance(source, str):
        raise ItemKeyError(f"a recipe is text, not a {type(source).__name__}")
    try:
        with warnings.catch_warnings():
            # A syntax warning changes nothing a recipe builds, so a filter that
            # turns warnings into errors must not turn the recipe into a refusal.
            warnings.simplefilter("ignore")
            return ast.parse(source)
    except (SyntaxError, ValueError, RecursionError) as error:
        # A null byte raises SyntaxError, a lone surrogate UnicodeEncodeError (a
        # ValueError), and nesting past the interpreter's limit RecursionError.
        raise ItemKeyError(f"cannot parse the recipe: {error}") from error


def _check_shape(tree: ast.Module) -> None:
    # Walked with a stack rather than recursion, so any depth is measured.
    stack = [(tree, 1)]
    while stack:
        node, depth = stack.pop()
        if depth > _NESTING_LIMIT:
            raise ItemKeyError(f"the recipe nests deeper than {_NESTING_LIMIT} levels")
        if (
            isinstance(node, ast.Constant)
            and type(node.value) is int
            and abs(node.value) >= _HUGE_INTEGER
        ):
            raise ItemKeyError("the recipe holds an integer literal of more than 640 digits")
        stack.extend((child, depth + 1) for child in ast.iter_child_nodes(node))


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
                node.body.append(ast.Pass())


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
    if _binds_params_elsewhere(tree, node.targets[0]):
        raise ItemKeyError("the recipe binds params outside its params literal; bind it only there")
    names: list[str] = []
    for key, value in zip(node.value.keys, node.value.values, strict=True):
        if key is None:
            raise ItemKeyError("the params literal unpacks another mapping; write each name out")
        name, default = _literal(key, "name"), _literal(value, "value")
        if not isinstance(name, str):
            raise ItemKeyError(f"params name {ast.unparse(key)} is not a string")
        if name in names:
            raise ItemKeyError(f"params name {_shown(name)} appears twice in the params literal")
        if not isinstance(default, _PARAM_VALUE_TYPES):
            raise ItemKeyError(
                f"params value {ast.unparse(value)} of {_shown(name)} is not a number, string or bool"
            )
        names.append(name)
    blank = ast.Assign(
        targets=[ast.Name(id=_PARAMS_NAME, ctx=ast.Store())],
        value=ast.Dict(
            keys=[ast.Constant(name) for name in names],
            values=[ast.Constant(...) for _ in names],
        ),
    )
    tree.body[bindings[0]] = ast.copy_location(blank, node)
    return tuple(names)


def _literal(node: ast.expr, what: str) -> object:
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError) as error:  # not a literal; an unhashable member
        raise ItemKeyError(f"params {what} {ast.unparse(node)} is not a literal") from error


def _binds_params(target: ast.expr) -> bool:
    return any(
        isinstance(node, ast.Name) and node.id == _PARAMS_NAME and isinstance(node.ctx, ast.Store)
        for node in ast.walk(target)
    )


def _binds_params_elsewhere(tree: ast.Module, literal_target: ast.Name) -> bool:
    # Any binding of the name besides the literal's own, in any scope: a store or a
    # del, an argument, a global or nonlocal declaration, a def or class, an import,
    # an except or match capture, or a type parameter.
    for node in ast.walk(tree):
        if node is literal_target:
            continue
        if isinstance(node, ast.Name):
            named = node.id if not isinstance(node.ctx, ast.Load) else None
        elif isinstance(node, ast.arg):
            named = node.arg
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            named = _PARAMS_NAME if _PARAMS_NAME in node.names else None
        elif isinstance(node, ast.alias):
            named = node.asname or node.name.partition(".")[0]
        elif isinstance(node, ast.MatchMapping):
            named = node.rest
        elif isinstance(node, _NAMED_BINDINGS):
            named = node.name
        else:
            named = None
        if named == _PARAMS_NAME:
            return True
    return False


# The nodes that bind the name they carry in a ``name`` field.
_NAMED_BINDINGS = (
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
    ast.ExceptHandler,
    ast.MatchAs,
    ast.MatchStar,
    ast.TypeVar,
    ast.ParamSpec,
    ast.TypeVarTuple,
)


#: The engine files beside this module that run a recipe and bake its solid: the
#: runtime and its allow-list, the code that writes the declared values into the
#: recipe, the worker that starts the run, the child that bakes the result, and the
#: tree that places and scales a build's parts. A leaf is keyed on their whole bytes
#: rather than on chosen functions in them, so no change to them can slip past the
#: key.
RUN_AND_BAKE_FILES = (
    "script_runtime.py",
    "api_subset.py",
    "params.py",
    "worker.py",
    "_worker_child.py",
    "assembly_tree.py",
)

# The geometry kernel, by the toolchain part and the top-level module each is.
_KERNEL = (("build123d", "build123d"), ("ocp", "OCP"))

# Where the hashed files are read from: beside this file as the package holds it,
# not beside whatever a symbolic link to it points at.
_PACKAGE_DIR = Path(__file__).absolute().parent


@_refused_when_too_deep
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
    is ``-`` and ``leaf_key`` refuses a recipe that draws text. The engine's version
    number is left out: a change to the engine's code that can alter a shape changes
    a hashed file, and a kernel upgrade a kernel part. The other packages build123d
    builds with are pinned by an engine release but not hashed, so a release that
    changes only them, or nothing that is hashed, keeps every key.

    Raises ``ItemKeyError`` when ``helpers`` is not a collection of names, for a
    helper ``HELPER_SOURCES`` does not list, a kernel module that no installed
    distribution provides, that more than one does, or that Python would import
    from a file its distribution did not install, or a hashed file that cannot be
    read.
    """
    if isinstance(helpers, (str, bytes)):
        raise ItemKeyError(
            f"helpers is a collection of helper names, not the one {_shown(helpers)}"
        )
    try:
        names = sorted(set(helpers))
    except TypeError as error:
        raise ItemKeyError(
            f"helpers is a collection of helper names, not {_shown(helpers)}"
        ) from error
    unknown = [name for name in names if name not in HELPER_SOURCES]
    if unknown:
        raise ItemKeyError(f"no engine helper is named {_shown(unknown[0])}")
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
        _check_installed_by(module, name)
        parts.append(f"{part}={name}=={metadata.version(name)}")
    return parts


def _check_installed_by(module: str, name: str) -> None:
    # The file Python imports the module from must be one the distribution installed;
    # otherwise its metadata does not describe the kernel that builds. find_spec
    # locates a top-level module without running it.
    try:
        spec = importlib.util.find_spec(module)
    except (ImportError, ValueError) as error:
        raise ItemKeyError(f"cannot locate the {module} module: {error}") from error
    distribution = metadata.distribution(name)
    files = distribution.files
    if spec is None or spec.origin is None or files is None:
        raise ItemKeyError(f"cannot tell whether {name} installed the {module} module")
    origin = Path(spec.origin).resolve()
    base = Path(distribution.locate_file("")).resolve()
    installed = {Path(file).as_posix() for file in files}
    if not origin.is_relative_to(base) or origin.relative_to(base).as_posix() not in installed:
        raise ItemKeyError(
            f"Python would import {module} from {spec.origin}, which {name} did not install"
        )


def _files_digest(names: Iterable[str]) -> str:
    # The sha256 of the canonical JSON mapping each file name to its bytes' sha256,
    # the files read in name order so a refusal names the same file every time.
    digests = {}
    for name in sorted(names):
        try:
            data = (_PACKAGE_DIR / name).read_bytes()
        except OSError as error:
            raise ItemKeyError(f"cannot read {name} beside the item key module: {error}") from error
        digests[name] = hashlib.sha256(data).hexdigest()
    return hashlib.sha256(jcs(digests)).hexdigest()


@_refused_when_too_deep
def leaf_key(
    source: str, params: Mapping[str, object], types: Mapping[str, str], toolchain: str
) -> str:
    """The key of a leaf: the solid ``source`` builds with ``params`` under ``toolchain``.

    The sha256, as lowercase hex, of the canonical recipe, a 0x00 byte, the
    canonical params, a 0x00 byte and ``toolchain`` encoded as UTF-8. None of the
    three holds a 0x00 byte, so no two inputs share those bytes. ``toolchain`` is
    what ``toolchain_identity(helpers_called(source))`` returns where the leaf is
    built.

    Raises ``ItemKeyError`` as ``canonical_recipe`` and ``canonical_params`` do;
    when the declared names are not the names in the recipe's params literal (an
    undeclared name would run with the literal's value, which no part of the key
    holds); when ``toolchain`` is not UTF-8 text free of 0x00 bytes with a
    ``helpers`` part, or that part disagrees with ``source`` about whether it
    calls a helper (so one leaf has one key); when its ``python`` part is not the
    running interpreter's major.minor, since the canonical recipe is that
    interpreter's ``ast.unparse``; and when the recipe draws text, since the font
    it would be drawn in is in no part of the key.
    """
    recipe, names = _canonical_recipe(source)
    canonical = canonical_params(params, types)
    if _draws_text(source):
        raise ItemKeyError(
            "the recipe draws text, and no font ships with the engine for recipes, so no "
            "key can say which font built it"
        )
    undeclared, absent = sorted(set(names) - set(types)), sorted(set(types) - set(names))
    if undeclared or absent:
        raise ItemKeyError(
            "the declared params must be the names in the recipe's params literal: "
            f"undeclared {undeclared}, not in the literal {absent}"
        )
    return hashlib.sha256(
        b"\x00".join([recipe, canonical, _toolchain(toolchain, source)])
    ).hexdigest()


def _draws_text(source: str) -> bool:
    # A text-drawing name counts used as a name (a store included), as an attribute
    # (build123d.Text) or through an import under another name (from build123d
    # import Text as T). Telling a recipe's own name from build123d's is not worth
    # the risk of keying a font-dependent solid.
    for node in ast.walk(_parse_recipe(source)):
        if isinstance(node, ast.Name) and node.id in _DRAWS_TEXT:
            return True
        if isinstance(node, ast.Attribute) and node.attr in _DRAWS_TEXT | _DRAWS_TEXT_ATTRIBUTES:
            return True
        if isinstance(node, ast.alias) and node.name.rpartition(".")[2] in _DRAWS_TEXT:
            return True
    return False


def _toolchain(toolchain: object, source: str) -> bytes:
    # ``toolchain`` as UTF-8, once it is checked against what ``source`` calls.
    if not isinstance(toolchain, str) or "\x00" in toolchain:
        raise ItemKeyError(f"a toolchain is text from toolchain_identity, not {_shown(toolchain)}")
    try:
        encoded = toolchain.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ItemKeyError(f"the toolchain {_shown(toolchain)} is not UTF-8 text") from error
    parts = dict(part.partition("=")[::2] for part in toolchain.split(";"))
    running = f"{sys.version_info.major}.{sys.version_info.minor}"
    if parts.get("python") != running:
        raise ItemKeyError(
            f"the toolchain names Python {parts.get('python')!r}, but this is Python {running}; "
            "compute a leaf's key on the Python its toolchain names"
        )
    if "helpers" not in parts:
        raise ItemKeyError(f"the toolchain {_shown(toolchain)} has no helpers part")
    if (parts["helpers"] != "-") != bool(helpers_called(source)):
        raise ItemKeyError(
            "the toolchain's helpers part disagrees with the recipe about whether it "
            "calls a helper; pass toolchain_identity(helpers_called(source))"
        )
    return encoded


@_refused_when_too_deep
def item_key(document: Mapping[str, object]) -> str:
    """The key of an item with members: the sha256, as lowercase hex, of ``document``.

    ``document`` is written as canonical JSON with its own top-level ``key`` field
    left out, so a document can carry its key and still be keyed. List each member
    with its key (and its pose in the form ``canonical_pose`` gives), so the item's
    key changes whenever a member's does, to any depth.

    Raises ``ItemKeyError`` when ``document`` is not a mapping, or as ``jcs`` does.
    """
    if not isinstance(document, Mapping):
        raise ItemKeyError(f"an item document is a mapping, not {_shown(document)}")
    content = {name: value for name, value in document.items() if name != "key"}
    return hashlib.sha256(jcs(content)).hexdigest()


@_refused_when_too_deep
def jcs(value: object) -> bytes:
    """``value`` as RFC 8785 canonical JSON, encoded as UTF-8.

    Accepts mappings with string keys, lists and tuples, strings, integers,
    floats, booleans and ``None``; an instance of a number or string subclass is
    written as the plain value it stands for. Object members are sorted by the
    UTF-16 code units of their keys, numbers take their shortest ECMAScript form,
    and nothing is written between tokens.

    Raises ``ItemKeyError`` for a non-finite number, an integer a double cannot
    hold exactly, a non-string key, two keys that are the same string, a string
    holding a lone surrogate, nesting deeper than 100 levels (a container that holds
    itself among them), or any other type.
    """
    return _write(value, 0).encode("utf-8")


def _write(value: object, depth: int) -> str:
    if depth > _NESTING_LIMIT:
        raise ItemKeyError(
            f"a value nested deeper than {_NESTING_LIMIT} levels has no canonical form"
        )
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return _json_number(_plain_number(value))
    if isinstance(value, str):
        return _string(str.__str__(value))
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_write(item, depth + 1) for item in value) + "]"
    if isinstance(value, Mapping):
        return _object(value, depth)
    raise ItemKeyError(f"cannot write a {type(value).__name__} as canonical JSON")


def _json_number(number: int | float) -> str:
    if isinstance(number, float):
        return _number(number)
    if abs(number) <= _SAFE_INTEGER:
        return str(number)
    # Past 2**53 - 1 an integer is a JSON number only if a double holds it exactly;
    # it is then written as that double is. Its digits are never printed here, since
    # the interpreter may refuse to write a long one.
    try:
        double = float(number)
    except OverflowError as error:
        raise ItemKeyError(
            f"an integer of {number.bit_length()} bits is past any double"
        ) from error
    if double != number:
        raise ItemKeyError(f"an integer of {number.bit_length()} bits is not one a double holds")
    return _number(double)


def _object(value: Mapping, depth: int) -> str:
    members: dict[str, object] = {}
    for key in value:
        if not isinstance(key, str):
            raise ItemKeyError(f"object key {_shown(key)} is not a string")
        name = str.__str__(key)
        if name in members:
            raise ItemKeyError(f"object key {_shown(name)} appears twice")
        members[name] = value[key]
    ordered = sorted(members, key=_code_units)
    return (
        "{"
        + ",".join(_string(name) + ":" + _write(members[name], depth + 1) for name in ordered)
        + "}"
    )


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
            raise ItemKeyError(
                f"string {_shown(text)} holds a lone surrogate, which has no UTF-8 form"
            )
        else:
            out.append(char)
    return '"' + "".join(out) + '"'


def _number(value: float) -> str:
    # ECMAScript's Number-to-String: the shortest digits that read back as the
    # same double, placed by the exponent n that ECMA-262 defines.
    if not math.isfinite(value):
        raise ItemKeyError(f"{_shown(value)} is not a finite number")
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
