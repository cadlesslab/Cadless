"""``constraints.txt`` still agrees with the ranges ``pyproject.toml`` declares.

The two files are written by hand and by ``make lock`` respectively, and
nothing else makes them agree. Raise a bound in pyproject.toml without
regenerating the lock and CI keeps installing the old version — so the range
that was widened on purpose never takes effect, and the build that was meant to
become repeatable silently stops matching what the ranges say. This is the only
thing that notices.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

_ROOT = Path(__file__).resolve().parent.parent
_PYPROJECT = _ROOT / "pyproject.toml"
_CONSTRAINTS = _ROOT / "constraints.txt"


def _declared() -> list[Requirement]:
    data = tomllib.loads(_PYPROJECT.read_text())
    project = data["project"]
    lines = list(project["dependencies"])
    for extra in project["optional-dependencies"].values():
        lines.extend(extra)
    return [Requirement(line) for line in lines]


def _pinned() -> dict[str, Version]:
    pins: dict[str, Version] = {}
    for line in _CONSTRAINTS.read_text().splitlines():
        if not line or line[0].isspace() or line.startswith("#"):
            continue
        name, _, version = line.partition("==")
        pins[canonicalize_name(name)] = Version(version.strip())
    return pins


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
