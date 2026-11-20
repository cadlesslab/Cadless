"""Static validation of generated code before execution.

Enforces the policy in :mod:`cadless.api_subset` via an AST walk — no code is
executed. Checks:
  * imports name an allow-listed module exactly -- no submodule, no relative
    import -- and a name taken from build123d or copy is one reviewed for
    scripts, never a private, banned, dunder or withheld one,
  * no banned builtin, dunder or withheld build123d name anywhere a name is
    written: a variable, a function, a parameter, a keyword, an import alias,
    a ``global`` or ``nonlocal``, an exception handler or a type parameter,
  * no builtin outside the scripts' allow-list read where the script does not
    define that name itself,
  * no dunder or private (``_x``) attribute, none of the interpreter's frame,
    generator, coroutine, traceback or code attributes, no method that looks
    attributes up from text, and no other refused or withheld attribute,
  * no attribute set or deleted but the few a script may write,
  * no class definitions and no ``match`` statements,
  * the script assigns the required ``result`` variable.

This is a *static* gate, on names alone. It pairs with the restricted namespace
scripts run in, and neither holds without the other: the namespace cannot take
back a frame a script reaches by attribute, and the gate cannot know which of a
module's names hold other modules.
"""

from __future__ import annotations

import ast
import builtins
from dataclasses import dataclass, field

from cadless.api_subset import (
    BANNED_NAMES,
    BUILD123D_REVIEWED,
    BUILD123D_WITHHELD,
    COPY_NAMES,
    INTERNALS_ATTRIBUTE_PREFIXES,
    REFUSED_ATTRIBUTES,
    RESULT_VARIABLE,
    SCRIPT_BUILTINS,
    STRING_LOOKUP_ATTRIBUTES,
    WRITABLE_ATTRIBUTES,
    is_module_allowed,
)

_WITHHELD = "build123d's file, network and font functions are not available"
_WRITABLE = ", ".join(f".{name}" for name in sorted(WRITABLE_ATTRIBUTES))

# What a script may take by name from a module it is not handed whole. math's
# public names are all plain numbers and functions, so it needs no list.
_NAMES_FROM = {"build123d": BUILD123D_REVIEWED - BUILD123D_WITHHELD, "copy": COPY_NAMES}

# The builtins of the interpreter running the gate, less the ones scripts get.
_UNAVAILABLE_BUILTINS = frozenset(dir(builtins)) - SCRIPT_BUILTINS


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


def _is_dunder(name: str) -> bool:
    return name.startswith("__") and name.endswith("__")


def _names_written(node: ast.AST) -> list[str]:
    """The identifiers ``node`` writes, attribute names and module paths aside."""
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return [node.name]
    if isinstance(node, ast.arg):
        return [node.arg]
    if isinstance(node, ast.keyword):
        return [node.arg] if node.arg else []
    if isinstance(node, ast.alias):
        return [node.asname] if node.asname else []
    if isinstance(node, (ast.Global, ast.Nonlocal)):
        return list(node.names)
    if isinstance(node, ast.ExceptHandler):
        return [node.name] if node.name else []
    if isinstance(node, (ast.TypeVar, ast.ParamSpec, ast.TypeVarTuple)):
        return [node.name]
    return []


def _names_bound(tree: ast.Module) -> set[str]:
    """Every name the script itself binds, in any scope.

    Scope is not tracked: a name bound anywhere counts as the script's own
    everywhere. That errs toward letting a builtin's name through, and a builtin
    let through here still meets the namespace, which does not have it.
    """
    bound: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load):
            bound.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, ast.arg):
            bound.add(node.arg)
        elif isinstance(node, ast.alias):
            bound.add(node.asname or node.name.split(".", 1)[0])
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
    return bound


def _annotation_names(tree: ast.Module) -> set[int]:
    """The ``id`` of every name inside an annotation.

    Scripts are compiled with annotations postponed, so a name there is never
    looked up: the builtins rule has nothing to guard in one.
    """
    annotations: list[ast.AST] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.returns:
            annotations.append(node.returns)
        elif isinstance(node, (ast.arg, ast.AnnAssign)) and node.annotation:
            annotations.append(node.annotation)
    return {
        id(name)
        for annotation in annotations
        for name in ast.walk(annotation)
        if isinstance(name, ast.Name)
    }


def _name_reason(name: str) -> str | None:
    if name in BANNED_NAMES:
        return f"banned name: {name}"
    if _is_dunder(name):
        return f"dunder name: {name}"
    if name in BUILD123D_WITHHELD:
        return f"withheld name: {name} ({_WITHHELD})"
    return None


def attribute_refusal(attr: str) -> str | None:
    """Why the gate refuses a script writing ``.attr``, or ``None`` where it does not."""
    if _is_dunder(attr):
        return f"dunder attribute access: .{attr}"
    if attr.startswith("_"):
        return f"private attribute access: .{attr}"
    if attr.startswith(INTERNALS_ATTRIBUTE_PREFIXES):
        return f"interpreter-internals attribute access: .{attr}"
    if attr in STRING_LOOKUP_ATTRIBUTES:
        return f"string-driven attribute lookup: .{attr} (build text with an f-string instead)"
    if attr in REFUSED_ATTRIBUTES:
        return f"attribute not available to scripts: .{attr}"
    if attr in BUILD123D_WITHHELD:
        return f"withheld attribute: .{attr} ({_WITHHELD})"
    return None


def _import_reasons(node: ast.Import | ast.ImportFrom) -> list[str]:
    if isinstance(node, ast.Import):
        return [
            f"disallowed import: {alias.name}"
            for alias in node.names
            if not is_module_allowed(alias.name)
        ]
    module = "." * node.level + (node.module or "")
    if node.level or not is_module_allowed(module):
        return [f"disallowed import: from {module}"]
    offered = _NAMES_FROM.get(module)
    reasons = []
    for alias in node.names:
        if alias.name == "*":
            continue
        reason = _name_reason(alias.name)
        if reason:
            reasons.append(reason)
        elif alias.name.startswith("_"):
            reasons.append(f"private import: {alias.name} from {module}")
        elif offered is not None and alias.name not in offered:
            reasons.append(f"not available to scripts: {alias.name} from {module}")
    return reasons


def validate_code(code: str) -> ValidationResult:
    """Return a ValidationResult; ok=False with reasons if the policy is violated."""
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return ValidationResult(ok=False, reasons=[f"syntax error: {exc.msg} (line {exc.lineno})"])
    # Refused rather than raised, all three: a gate that cannot read a script has
    # not passed it.
    except RecursionError:
        return ValidationResult(ok=False, reasons=["syntax error: nested too deeply to check"])
    except MemoryError:
        return ValidationResult(ok=False, reasons=["syntax error: too large to check"])
    except ValueError as exc:
        # Text the parser cannot encode, such as a lone surrogate.
        return ValidationResult(ok=False, reasons=[f"syntax error: unreadable source ({exc})"])

    reasons: list[str] = []
    bound = _names_bound(tree)
    annotated = _annotation_names(tree)

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            reasons.extend(_import_reasons(node))
        elif isinstance(node, ast.Attribute):
            reason = attribute_refusal(node.attr)
            if reason:
                reasons.append(reason)
            elif not isinstance(node.ctx, ast.Load) and node.attr not in WRITABLE_ATTRIBUTES:
                reasons.append(
                    f"attribute write not allowed: .{node.attr} (a script sets only "
                    f"{_WRITABLE}; build new geometry instead of changing an object)"
                )
        elif isinstance(node, ast.ClassDef):
            reasons.append("class definitions are not allowed (write functions instead)")
        elif isinstance(node, ast.Match):
            reasons.append("match statements are not allowed (use if/elif instead)")
        for name in _names_written(node):
            reason = _name_reason(name)
            if reason:
                reasons.append(reason)
            elif (
                isinstance(node, ast.Name)
                and isinstance(node.ctx, ast.Load)
                and name in _UNAVAILABLE_BUILTINS
                and name not in bound
                and id(node) not in annotated
            ):
                reasons.append(f"builtin not available to scripts: {name}")

    if not _assigns_result(tree):
        reasons.append(f"missing required `{RESULT_VARIABLE}` assignment")

    # De-duplicate while preserving order.
    seen: set[str] = set()
    deduped = [r for r in reasons if not (r in seen or seen.add(r))]
    return ValidationResult(ok=not deduped, reasons=deduped)
