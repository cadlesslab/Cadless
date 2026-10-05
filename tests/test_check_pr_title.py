"""Tests for the pull-request title check (tools/check_pr_title.py)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tools.check_pr_title import conforms

_ROOT = Path(__file__).resolve().parent.parent
_CHECK = _ROOT / "tools" / "check_pr_title.py"

ACCEPTED = {
    "plain": "feat: let a model backend be installed beside the engine",
    "scope": "fix(worker): stop the export scale leaking into the next run",
    "breaking": "feat!: drop the old provider registry",
    "scope and breaking": "fix(catalog)!: rename the artifact key",
    "this repository's own type": "improve: say why a part was refused",
    "the release pull request": "chore(main): release 1.1.0",
}

REFUSED = {
    "no type": "Add a thing",
    "capitalised type": "Feat: add a thing",
    "unknown type": "feature: add a thing",
    "no space after the colon": "feat:add a thing",
    "two spaces after the colon": "feat:  add a thing",
    "empty description": "feat: ",
    "empty scope": "feat(): add a thing",
    "space before the scope": "feat (worker): add a thing",
}


def test_accepts_every_conforming_title():
    for label, title in ACCEPTED.items():
        assert conforms(title), f"{label!r} should conform: {title!r}"


def test_refuses_every_nonconforming_title():
    for label, title in REFUSED.items():
        assert not conforms(title), f"{label!r} should be refused: {title!r}"


def _cli(*args: str) -> int:
    return subprocess.run([sys.executable, str(_CHECK), *args], capture_output=True).returncode


def test_cli_passes_a_conforming_title(tmp_path):
    title = tmp_path / "title.txt"
    title.write_text(ACCEPTED["plain"] + "\n", encoding="utf-8")
    assert _cli(str(title)) == 0


def test_cli_fails_a_nonconforming_title(tmp_path):
    title = tmp_path / "title.txt"
    title.write_text(REFUSED["unknown type"] + "\n", encoding="utf-8")
    assert _cli(str(title)) == 1


def test_cli_fails_closed_on_a_title_it_could_not_read(tmp_path):
    assert _cli(str(tmp_path / "absent.txt")) == 1


def test_cli_refuses_to_run_without_a_title():
    """A miswired call names no file; answering "conforms" would hide the miswiring."""
    assert _cli() == 2
