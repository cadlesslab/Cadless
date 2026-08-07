"""Structured parameter block.

Generated build123d scripts declare their editable dimensions in a single
module-level ``params = {...}`` literal that the body then references, e.g.::

    from build123d import *

    params = {"length": 40, "width": 20, "hole_dia": 6}
    plate = Box(params["length"], params["width"], 8)
    result = plate - Cylinder(radius=params["hole_dia"] / 2, height=8)

Because the block is a plain literal, we can (a) read the named dimensions out
of any generated script and (b) re-run the model with overridden values by
splicing a new literal in — a pure, deterministic substitution that needs no
second LLM call. Both operations are intentionally conservative: anything that
is not a clean module-level literal dict is treated as "no parameters" rather
than guessed at.
"""

from __future__ import annotations

import ast

PARAMS_VARIABLE = "params"

# Literal value types accepted as parameter values / overrides.
_VALUE_TYPES = (int, float, str, bool)


def extract_params(code: str) -> dict:
    """Return the ``params`` literal from ``code``, or ``{}`` if there isn't one.

    Only a module-level ``params = {<literal dict>}`` assignment is recognised;
    unparseable code or a non-literal/non-dict value yields ``{}`` (so older
    scripts without a block remain valid, just non-parametric).
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return {}
    node = _find_params_assignment(tree)
    if node is None:
        return {}
    try:
        value = ast.literal_eval(node.value)
    except (ValueError, SyntaxError):
        return {}
    return value if isinstance(value, dict) else {}


def apply_param_overrides(code: str, overrides: dict) -> str:
    """Return ``code`` with its ``params`` literal updated by ``overrides``.

    The substitution preserves the rest of the script verbatim. Raises
    ``ValueError`` if the script has no literal ``params`` dict, if an override
    names a parameter the script doesn't declare, or if an override value is not
    a number, string, or boolean.
    """
    tree = ast.parse(code)
    node = _find_params_assignment(tree)
    if node is None:
        raise ValueError("script has no `params` block to override")
    current = ast.literal_eval(node.value)
    if not isinstance(current, dict):
        raise ValueError("`params` is not a literal dict")

    unknown = [k for k in overrides if k not in current]
    if unknown:
        raise ValueError("unknown parameter(s): " + ", ".join(sorted(unknown)))
    bad = [k for k, v in overrides.items() if not isinstance(v, _VALUE_TYPES)]
    if bad:
        raise ValueError(
            "override values must be numbers, strings, or booleans: " + ", ".join(sorted(bad))
        )

    merged = {**current, **overrides}  # existing keys keep their position
    return _splice(code, node, f"{PARAMS_VARIABLE} = {merged!r}")


def _find_params_assignment(tree: ast.Module) -> ast.Assign | None:
    """First module-level ``params = {<dict>}`` assignment, if any."""
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == PARAMS_VARIABLE
            and isinstance(node.value, ast.Dict)
        ):
            return node
    return None


def _splice(code: str, node: ast.AST, replacement: str) -> str:
    """Replace the source span of ``node`` with ``replacement``."""
    lines = code.splitlines(keepends=True)
    start = _offset(lines, node.lineno, node.col_offset)
    end = _offset(lines, node.end_lineno, node.end_col_offset)
    return code[:start] + replacement + code[end:]


def _offset(lines: list[str], lineno: int, col: int) -> int:
    """Character offset into the source for a 1-based line / 0-based column."""
    return sum(len(line) for line in lines[: lineno - 1]) + col
