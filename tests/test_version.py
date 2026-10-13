"""The backend's version is written once, and everything that reports it reads it.

A backend release moves ``cadless.__version__``. Any copy of the number the
release does not know about would keep the old one through every release that
followed, and nothing would fail to say so. The frontend is released on its own
and keeps its version in one file of its own, never in the npm package.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from pathlib import Path

import cadless
from backend.app import create_app
from cadless.store import Store

ROOT = Path(__file__).resolve().parent.parent

# Not a number any release will carry, so a hard-coded copy can never match it.
SENTINEL = "0.0.0-sentinel"


def test_the_api_reports_the_package_version(tmp_path, monkeypatch):
    monkeypatch.setattr(cadless, "__version__", SENTINEL)
    store = Store(db_path=tmp_path / "db.sqlite", artifacts_dir=tmp_path / "artifacts")
    assert create_app(store=store).version == SENTINEL


def test_the_worker_reports_the_package_version():
    # A fresh interpreter, because the worker builds its app as the module is
    # imported: patching the version after that import would test nothing.
    probe = (
        f"import cadless; cadless.__version__ = {SENTINEL!r}; "
        "from worker.service import app; print(app.version)"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], cwd=ROOT, capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == SENTINEL


def test_the_built_package_takes_its_version_from_the_source():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "version" not in project["project"]
    assert project["project"]["dynamic"] == ["version"]
    assert project["tool"]["hatch"]["version"]["path"] == "cadless/__init__.py"


def test_the_npm_package_carries_no_copy_of_the_frontend_version():
    package = json.loads((ROOT / "frontend/package.json").read_text(encoding="utf-8"))
    lock = json.loads((ROOT / "frontend/package-lock.json").read_text(encoding="utf-8"))
    assert "version" not in package
    assert "version" not in lock
    assert "version" not in lock["packages"][""]
