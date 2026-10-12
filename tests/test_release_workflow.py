"""The release workflow and its configuration hold what the release depends on.

Nothing here can run the release itself — it happens on GitHub, after a merge —
so a slip in any of them would surface only on the first release pull request
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
from tests.workflow_text import wiring

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github/workflows"
WORKFLOW = WORKFLOWS / "release.yml"
CONFIG = ROOT / "release-please-config.json"
MANIFEST = ROOT / ".release-please-manifest.json"
FRONTEND_VERSION = ROOT / "frontend/version.txt"

_USES = re.compile(r"^\s*(?:-\s+)?uses:\s*(\S+)", re.MULTILINE)


def _wiring(path: Path = WORKFLOW) -> str:
    return wiring(path)


def _config() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def test_every_action_is_pinned_to_a_full_commit():
    """These jobs hold tokens that can push, tag and publish here, and a tag runs
    whatever it points at today. A workflow of this repository's own is the
    exception: it is read from the same commit. Every release workflow is found
    by its name, so a new one cannot slip past this check."""
    refs = [
        (workflow.name, ref)
        for workflow in sorted(WORKFLOWS.glob("release*.yml"))
        for ref in _USES.findall(wiring(workflow))
        if not ref.startswith("./")
    ]
    assert refs, "no action found, so the check below would pass on nothing"
    for workflow, ref in refs:
        name, _, rev = ref.partition("@")
        assert re.fullmatch(r"[0-9a-f]{40}", rev), f"{workflow}: {name} is referenced by {rev!r}"


def test_only_main_can_reach_the_key():
    """A run from another branch could mint the token, or cut a release nobody merged to main.

    A fork's run has neither the App nor its key, so it is skipped rather than
    left to fail at minting the token.
    """
    wiring = _wiring()
    assert "github.repository == 'cadlesslab/Cadless' && github.ref == 'refs/heads/main'" in wiring
    assert "environment: release" in wiring


def test_the_release_manifest_agrees_with_both_components():
    """Each component's release counts from the manifest and bumps its own version.

    If a pair disagrees, that component's next release pull request proposes a
    version computed from a number the component no longer carries.
    """
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    frontend = FRONTEND_VERSION.read_text(encoding="utf-8").strip()
    assert manifest == {".": cadless.__version__, "frontend": frontend}


def test_a_frontend_change_releases_only_the_frontend():
    """The root package is given every commit, the frontend's too, unless it excludes them.

    Without the exclusion, a change that touched nothing but the frontend would
    cut a backend release announcing it. An exclusion on the frontend's own
    package would stop the frontend releasing at all.
    """
    config = _config()
    packages = config["packages"]
    assert set(packages["."].get("exclude-paths", [])) == set(packages) - {"."}
    assert "exclude-paths" not in packages["frontend"]
    assert "exclude-paths" not in config, "a top-level exclusion is inherited by every package"


def test_each_component_tags_under_its_own_name():
    """Two components both releasing `vX.Y.Z` would race for the same tag.

    The workflow finds a component's last release by the name in its tags, and a
    consumer pins that name. Renaming a component, or turning the name off for
    one package, loses its history — its next changelog starts again from the
    beginning — and strands every pin.
    """
    config = _config()
    packages = config["packages"]
    assert config.get("include-component-in-tag") is True
    assert not any("include-component-in-tag" in package for package in packages.values())
    components = {path: package.get("component") for path, package in packages.items()}
    assert components == {".": "backend", "frontend": "frontend"}


def test_each_component_has_its_own_release_pull_request():
    """With more than one package, release-please defaults to one pull request for all.

    Merging that one would release both components together, which is what
    splitting them was for.
    """
    assert _config().get("separate-pull-requests") is True


def test_a_release_stays_a_draft_until_its_artifacts_are_attached():
    """With immutable releases on, nothing can be attached to a published release.

    Published at once, a release could never carry its artifacts. As a draft it
    would have no tag unless one is forced, and the build checks out the tag.
    """
    config = _config()
    assert config.get("draft") is True
    assert config.get("force-tag-creation") is True


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
