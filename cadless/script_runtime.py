"""The namespace a generated script runs in, and the one way scripts are run.

:func:`run_script` runs the static gate itself before it executes anything, so
code no caller validated is held to the same policy as a fresh script. The
script then runs with:

  * the builtins :data:`cadless.api_subset.SCRIPT_BUILTINS` names, a ``print``
    that writes to stderr, and an ``__import__`` that hands out nothing but
    views of the allowed modules;
  * those views, not the modules: a new module object per script, and never
    one holding a module. build123d's holds the names reviewed for scripts
    that the installed build123d has, less the withheld ones -- a name a later
    release adds is not handed out until it is reviewed; math's, its public
    names; copy's, ``Error``, ``copy`` and a ``deepcopy`` that takes no memo. A
    view's name is no ``sys.modules`` key, so ``from build123d import <name>``
    finds nothing the view does not hold -- the import machinery falls back to
    ``sys.modules`` under the package's name, and would find a submodule.

The namespace alone is not a boundary: a frame a script reaches by attribute
still carries the real builtins. The gate is what keeps frames out of reach,
which is why it runs in the same call, and why neither is left to the caller.

Imports build123d, for its view: import it where scripts run, not where they
are only validated.
"""

from __future__ import annotations

import builtins
import copy
import math
import sys
import types

import build123d

from cadless.api_subset import (
    BUILD123D_REVIEWED,
    BUILD123D_WITHHELD,
    COPY_NAMES,
    SCRIPT_BUILTINS,
    WRITABLE_ATTRIBUTES,
)
from cadless.joinery import script_namespace
from cadless.validation import validate_code

# Scripts have always been compiled with postponed annotations; a script that
# annotates with a name it never defines runs as it did. ``annotations`` is the
# feature the ``__future__`` import above binds.
_COMPILE_FLAGS = annotations.compiler_flag


class ScriptRefused(ValueError):
    """The static gate refused a script before it ran; ``reasons`` says why."""

    def __init__(self, reasons: list[str]) -> None:
        super().__init__("; ".join(reasons))
        self.reasons = list(reasons)


def _view(name: str, values: dict[str, object]) -> types.ModuleType:
    """A module object holding ``values``, minus any that is a module."""
    view = types.ModuleType(f"<script {name}>")
    kept = {key: value for key, value in values.items() if not isinstance(value, types.ModuleType)}
    for key, value in kept.items():
        setattr(view, key, value)
    view.__all__ = sorted(kept)
    return view


def _deepcopy(x):
    """``copy.deepcopy`` without its ``memo``.

    A memo the caller passes is handed back holding the originals the copy
    walked through, private attributes' values among them.
    """
    return copy.deepcopy(x)


def _shown() -> set[str]:
    return (BUILD123D_REVIEWED - BUILD123D_WITHHELD) & set(build123d.__all__)


def _own_copy(value: object) -> object:
    """``value``, or a copy of it where it is a dict a script could change in place."""
    return dict(value) if isinstance(value, dict) else value


def _views() -> dict[str, types.ModuleType]:
    copies = {"Error": copy.Error, "copy": copy.copy, "deepcopy": _deepcopy}
    return {
        "build123d": _view(
            "build123d", {name: _own_copy(getattr(build123d, name)) for name in _shown()}
        ),
        "math": _view(
            "math", {name: getattr(math, name) for name in dir(math) if not name.startswith("_")}
        ),
        "copy": _view("copy", {name: copies[name] for name in COPY_NAMES}),
    }


def _print(*values, sep=" ", end="\n", file=None, flush=False):  # noqa: ARG001 - `file` is ignored
    """``print`` for scripts: always to stderr, whatever ``file`` says.

    Stdout carries the worker child's result line, so nothing a script prints
    can be read back as one.
    """
    builtins.print(*values, sep=sep, end=end, file=sys.stderr, flush=flush)


def _import_guard(views: dict[str, types.ModuleType]):
    def __import__(name, globals=None, locals=None, fromlist=(), level=0):  # noqa: A002, N807
        if level == 0 and name in views:
            return views[name]
        raise ImportError(f"import of {name!r} is not allowed")

    return __import__


def script_globals() -> dict[str, object]:
    """The globals a script starts with: its helpers, and its builtins.

    A new dict, with new views, on every call: a script that rebinds a name in
    one of them changes nothing the next script, or the engine, reads. What the
    views hold is the engine's own, though -- the classes, functions and dicts
    themselves -- so a script that changes one of those changes it for every
    script after it in the same process.
    """
    granted = {name: getattr(builtins, name) for name in SCRIPT_BUILTINS}
    granted["print"] = _print
    granted["__import__"] = _import_guard(_views())
    namespace = script_namespace()
    namespace["__builtins__"] = granted
    return namespace


def _classes_handed_out() -> list[type]:
    """build123d's classes a script is handed, with every class they inherit from."""
    found: dict[type, None] = {}
    for name in _shown():
        value = getattr(build123d, name)
        if isinstance(value, type):
            for klass in value.__mro__:
                if klass.__module__ != "builtins":
                    found.setdefault(klass)
    return list(found)


# What a class held under a name it did not own: told apart from holding ``None``.
_ABSENT = object()

# A class's state as a script could change it: the value under each writable
# name, and each public container it holds with a shallow copy of its contents.
_Held = tuple[dict[str, object], dict[str, tuple[object, object]]]


def _shallow(container):
    return dict(container) if isinstance(container, dict) else type(container)(container)


def _contents(classes: list[type]) -> dict[type, _Held]:
    """What a script could change on each class: the writable names, and the containers.

    The writable names, because the gate lets a script set no other; and every
    public dict, list or set a class holds, because a script changes those in
    place -- ``Solid.shape_properties_LUT[...] = ...`` is a subscript, not an
    attribute -- and build123d reads its measurements through some of them.
    Nothing else: the interpreter and the library keep caches of their own on
    classes (``copy`` records ``__slotnames__`` the first time it copies an
    object), which a wider look would take for the script's doing. The values
    themselves are kept, not their ids, so nothing freed meanwhile can come back
    at the same address.
    """
    held = {}
    for klass in classes:
        own = vars(klass)
        names = {name: own.get(name, _ABSENT) for name in WRITABLE_ATTRIBUTES}
        containers = {
            name: (value, _shallow(value))
            for name, value in own.items()
            if not name.startswith("_") and isinstance(value, (dict, list, set))
        }
        held[klass] = (names, containers)
    return held


def _same(kept, now) -> bool:
    """Whether a container holds what its kept copy does, item for item."""
    if isinstance(kept, dict):
        return kept.keys() == now.keys() and all(now[key] is kept[key] for key in kept)
    if isinstance(kept, list):
        return len(kept) == len(now) and all(a is b for a, b in zip(kept, now, strict=True))
    return kept == now


def _changed(before: dict[type, _Held]) -> list[str]:
    """Each change a script made to a class, as the reason the repair loop reads."""
    changed = []
    for klass, (names, containers) in before.items():
        own = vars(klass)
        changed += [
            f"{klass.__name__}.{name} set on the class (set it on an object instead)"
            for name, value in sorted(names.items())
            if own.get(name, _ABSENT) is not value
        ]
        changed += [
            f"{klass.__name__}.{name} changed (build123d's tables are not the script's to change)"
            for name, (container, kept) in sorted(containers.items())
            if own.get(name, _ABSENT) is not container or not _same(kept, container)
        ]
    return changed


def _restore(before: dict[type, _Held]) -> None:
    """Put back what a refused script changed, for whatever runs after it in this process."""
    for klass, (names, containers) in before.items():
        for name, value in names.items():
            if vars(klass).get(name, _ABSENT) is not value:
                if value is _ABSENT:
                    delattr(klass, name)
                else:
                    setattr(klass, name, value)
        for name, (container, kept) in containers.items():
            if vars(klass).get(name, _ABSENT) is not container:
                setattr(klass, name, container)
            if not _same(kept, container):
                container.clear()
                if isinstance(container, list):
                    container.extend(kept)
                else:
                    container.update(kept)


def run_script(code: str, filename: str) -> dict[str, object]:
    """Gate, then run, ``code``; return the namespace it ran in.

    Compiled first, so a script that does not parse fails as it always has,
    with the location on its ``SyntaxError``. Raises :class:`ScriptRefused` when
    the gate refuses the script, and when the script changed one of build123d's
    classes -- a placement set on a class rather than an object, or a table the
    class holds -- since that changes how every part of the kind is read
    afterwards. What it changed is put back whether the script finished or
    raised, so nothing run after it in the same process starts from it; a
    script that raised after changing a class is refused, its own exception
    chained. Anything else the script raises propagates.
    """
    compiled = compile(code, filename, "exec", flags=_COMPILE_FLAGS, dont_inherit=True)
    verdict = validate_code(code)
    if not verdict.ok:
        raise ScriptRefused(verdict.reasons)
    namespace = script_globals()
    before = _contents(_classes_handed_out())
    try:
        exec(compiled, namespace)  # noqa: S102 - the gate ran, and the builtins are the allow-list
    finally:
        changed = _changed(before)
        if changed:
            _restore(before)
            raise ScriptRefused([f"changed build123d's own classes: {'; '.join(changed)}"])
    return namespace
