"""Static validation of generated code before execution.

Enforces the policy in :mod:`cadless.api_subset` via an AST walk — no code is
executed. Checks:
  * only allow-listed modules are imported,
  * no banned builtin names (eval/exec/open/__import__/getattr/...),
  * no dunder attribute access (blocks ``().__class__.__bases__`` escapes),
  * the script assigns the required ``result`` variable.

This is a *static* gate; it pairs with the resource-limited execution worker
 for defence in depth.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

from cadless.api_subset import (
    BANNED_NAMES,
    RESULT_VARIABLE,
    is_module_allowed,
)


@dataclass
class ValidationResult:
    ok: bool
    reasons: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:  # allow `if validate_code(...):`
        return self.ok


def _assigns_result(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            targets = [node.target]
        elif isinstance(node, ast.NamedExpr):
            targets = [node.target]
        for t in targets:
            if isinstance(t, ast.Name) and t.id == RESULT_VARIABLE:
                return True
            # tuple unpacking: result, x = ...
            if isinstance(t, (ast.Tuple, ast.List)):
                for elt in t.elts:
                    if isinstance(elt, ast.Name) and elt.id == RESULT_VARIABLE:
                        return True
    return False


def validate_code(code: str) -> ValidationResult:
    """Return a ValidationResult; ok=False with reasons if the policy is violated."""
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return ValidationResult(ok=False, reasons=[f"syntax error: {exc.msg} (line {exc.lineno})"])

    reasons: list[str] = []

    for node in ast.walk(tree):
        # Imports
        if isinstance(node, ast.Import):
            for alias in node.names:
                if not is_module_allowed(alias.name):
                    reasons.append(f"disallowed import: {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if not is_module_allowed(module):
                reasons.append(f"disallowed import: from {module}")
        # Banned builtin names (used anywhere)
        elif isinstance(node, ast.Name):
            if node.id in BANNED_NAMES:
                reasons.append(f"banned name: {node.id}")
        # Dunder attribute access
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("__") and node.attr.endswith("__"):
                reasons.append(f"dunder attribute access: .{node.attr}")

    if not _assigns_result(tree):
        reasons.append(f"missing required `{RESULT_VARIABLE}` assignment")

    # De-duplicate while preserving order.
    seen: set[str] = set()
    deduped = [r for r in reasons if not (r in seen or seen.add(r))]
    return ValidationResult(ok=not deduped, reasons=deduped)
