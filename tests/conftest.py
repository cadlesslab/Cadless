"""Fixtures more than one test module asks for.

Only the synthetic catalogue origin so far. It lives here rather than being
imported into each module that wants it because a fixture imported by name and
then named as a parameter reads to a linter as a redefinition — and because
pytest's own way of sharing a fixture is this file.

The origin's constants and its reader stay in `tests/depot_origin.py`, which
several modules import directly for the values they assert against.
"""

from __future__ import annotations

from tests.depot_origin import depot_origin  # noqa: F401 — re-exported as a fixture

__all__ = ["depot_origin"]
