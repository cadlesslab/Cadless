"""``constraints.txt`` still agrees with the ranges ``pyproject.toml`` declares.

The two files are written by hand and by ``make lock`` respectively, and nothing
else makes them agree.

Membership alone is not enough to notice a drift, which is worth stating because
it is the trap: *widen* a bound and every pin still satisfies it, so a guard that
only asks "is the pin inside the range" stays green while the declared contract
changes under it. That is why ``make lock`` records the declarations it was
generated from in the file's header, and why this module reads those through the
same function that writes them.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

from tools.lock_header import declaration_lines

_ROOT = Path(__file__).resolve().parent.parent
_PYPROJECT = _ROOT / "pyproject.toml"
_CONSTRAINTS = _ROOT / "constraints.txt"


def _declared() -> list[Requirement]:
    return [Requirement(line) for line in declaration_lines(_PYPROJECT)]


def _pinned() -> dict[str, Version]:
    pins: dict[str, Version] = {}
    for line in _CONSTRAINTS.read_text().splitlines():
        if not line or line[0].isspace() or line.startswith("#"):
            continue
        name, _, version = line.partition("==")
        pins[canonicalize_name(name)] = Version(version.strip())
    return pins


def _recorded_declarations() -> list[str]:
    return [
        line[4:].strip()
        for line in _CONSTRAINTS.read_text().splitlines()
        if line.startswith("#   ")
    ]


def test_the_lock_records_the_declarations_it_was_generated_from():
    """Catches a bound edited without re-locking, widening included."""
    assert _recorded_declarations() == declaration_lines(_PYPROJECT), (
        "constraints.txt was generated from different declarations — run `make lock`"
    )


def test_every_declared_dependency_is_pinned_in_the_constraints_file():
    pins = _pinned()
    missing = sorted({r.name for r in _declared() if canonicalize_name(r.name) not in pins})
    assert not missing, f"declared but absent from constraints.txt: {missing} — run `make lock`"


def test_every_pinned_version_satisfies_its_declared_range():
    pins = _pinned()
    violations = [
        f"{r.name}{r.specifier} is pinned at {pins[canonicalize_name(r.name)]}"
        for r in _declared()
        if canonicalize_name(r.name) in pins and pins[canonicalize_name(r.name)] not in r.specifier
    ]
    assert not violations, (
        f"constraints.txt contradicts pyproject.toml: {violations} — run `make lock`"
    )


def test_every_runtime_and_dev_dependency_carries_an_upper_bound():
    """No bare lower bound may come back.

    An unbounded pin is what let a major SDK release change what an unchanged
    tree installs. The lock file hides that from a build but not from a
    dependent project, which resolves against these ranges and never sees
    constraints.txt at all.
    """
    unbounded = sorted(
        r.name
        for r in _declared()
        if not any(s.operator in ("<", "<=", "==", "~=") for s in r.specifier)
    )
    assert not unbounded, f"no upper bound on: {unbounded}"


def test_the_build_backend_is_bounded_too():
    """``-c`` never reaches it, so only the declaration can.

    pip installs PEP 517 build requirements into an isolated environment built
    with no constraint file, so the one dependency that builds the package is
    the one the lock cannot cover.
    """
    build = tomllib.loads(_PYPROJECT.read_text())["build-system"]["requires"]
    unbounded = [
        line
        for line in build
        if not any(s.operator in ("<", "<=", "==", "~=") for s in Requirement(line).specifier)
    ]
    assert not unbounded, f"build-system requirement has no upper bound: {unbounded}"
