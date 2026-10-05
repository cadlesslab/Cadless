"""The release workflow and its configuration hold what the release depends on.

Nothing here can run the release itself — it happens on GitHub, after a merge —
so a slip in either file would surface only on the first release pull request
after it, as a failed check or a release that never comes.

Plain string checks on the workflow rather than a YAML parse, because PyYAML is
not a dependency of this package and adding one to read a few lines would cost
more than it says.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import cadless

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github/workflows/release.yml"
CONFIG = ROOT / "release-please-config.json"

_USES = re.compile(r"^\s*(?:-\s+)?uses:\s*(\S+)", re.MULTILINE)


def _wiring() -> str:
    """The workflow with its comment lines removed, so prose cannot satisfy an assertion."""
    text = WORKFLOW.read_text(encoding="utf-8")
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def _config() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def test_every_action_is_pinned_to_a_full_commit():
    """The job holds a token that can push here, and a tag runs whatever it points at today."""
    actions = _USES.findall(_wiring())
    assert actions, "no `uses:` line found, so the check below would pass on nothing"
    for ref in actions:
        name, _, rev = ref.partition("@")
        assert re.fullmatch(r"[0-9a-f]{40}", rev), f"{name} is referenced by {rev!r}, not a commit"


def test_only_main_can_reach_the_key():
    """A run from another branch could mint the token, or cut a release nobody merged to main."""
    wiring = _wiring()
    assert "github.ref == 'refs/heads/main'" in wiring
    assert "environment: release" in wiring


def test_the_release_manifest_agrees_with_the_package():
    """The release counts from the manifest and bumps the package.

    If the two disagree, the next release pull request proposes a version
    computed from a number the package no longer carries.
    """
    manifest = json.loads((ROOT / ".release-please-manifest.json").read_text(encoding="utf-8"))
    assert manifest == {".": cadless.__version__}


def test_the_release_commit_is_signed_off():
    """The DCO check is required on main and has no exemption for a bot."""
    assert re.fullmatch(r"[^<>]+<[^<>\s]+>", _config().get("signoff", ""))


def test_the_types_that_release_are_the_documented_ones():
    """A section marked hidden prints nothing, and a release with nothing to print never opens.

    CONTRIBUTING.md's table of what each type does to the next release reads off
    this set; hiding a section here would quietly stop that type releasing.
    """
    visible = {s["type"] for s in _config()["changelog-sections"] if not s.get("hidden")}
    assert visible == {"feat", "fix", "improve", "perf", "revert"}


def test_the_types_a_title_may_carry_are_the_documented_ones():
    """The title check takes its types from these sections, so removing one refuses its titles."""
    types = {s["type"] for s in _config()["changelog-sections"]}
    assert types == {
        "feat",
        "fix",
        "improve",
        "perf",
        "revert",
        "refactor",
        "docs",
        "test",
        "build",
        "ci",
        "chore",
    }
