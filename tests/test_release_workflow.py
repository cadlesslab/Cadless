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

import pytest

import cadless
from tests.workflow_text import job, wiring

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github/workflows"
CONFIG = ROOT / "release-please-config.json"
MANIFEST = ROOT / ".release-please-manifest.json"
FRONTEND_VERSION = ROOT / "frontend/version.txt"

_USES = re.compile(r"^\s*(?:-\s+)?uses:\s*(\S+)", re.MULTILINE)


def _config() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def _release_workflows() -> list[Path]:
    """Every release workflow by its name, under either extension GitHub reads."""
    return sorted(
        path
        for path in WORKFLOWS.iterdir()
        if path.name.startswith("release") and path.suffix in {".yml", ".yaml"}
    )


def test_every_action_is_pinned_to_a_full_commit():
    """These jobs hold tokens that can push, tag and publish here, and a tag runs
    whatever it points at today. A workflow of this repository's own is the
    exception: it is read from the same commit. Every release workflow is found
    by its name, so a new one cannot slip past this check."""
    refs = [
        (workflow.name, ref)
        for workflow in _release_workflows()
        for ref in _USES.findall(wiring(workflow))
        if not ref.startswith("./")
    ]
    assert refs, "no action found, so the check below would pass on nothing"
    for workflow, ref in refs:
        name, _, rev = ref.partition("@")
        assert re.fullmatch(r"[0-9a-f]{40}", rev), f"{workflow}: {name} is referenced by {rev!r}"


_GUARD = "if: github.repository == 'cadlesslab/Cadless' && github.ref == 'refs/heads/main'"

# Each workflow a release run can start from, with its entry job and whether
# that job itself runs in the release environment.
_ENTRIES = {
    "release.yml": ("release-please", True),
    "release-backfill.yml": ("resolve", False),
}

# A status function lets a job run after the jobs it needs were skipped, so each
# condition that uses one is held whole. The backfill's attach runs when the
# images were skipped for the frontend, and only after its artifacts succeeded.
_STATUS_CONDITIONS = {
    "if: ${{ !cancelled() && needs.artifacts.result == 'success' && "
    "(needs.images.result == 'success' || needs.images.result == 'skipped') }}"
}

_ENVIRONMENT = re.compile(r"^    environment:(?: release|\n      name: release)$", re.M)
# GitHub reads expression function names in any case.
_STATUS = re.compile(r"\b(always|cancelled|failure|success)\(\)", re.I)


def _triggers(path: Path) -> set[str]:
    """The events under the workflow's `on:` key, read from the lines indented under it.

    A line there that is not an event key, or no event read at all, is refused
    rather than read as no trigger: a workflow this cannot read is one it would
    otherwise leave out of the entries.
    """
    on = wiring(path).split("\njobs:\n", 1)[0].split("\non:\n", 1)[1]
    keys = set()
    for line in on.splitlines():
        if line and not line.startswith(" "):
            break
        if re.match(r"  \S", line):
            match = re.fullmatch(r"  ([\w-]+):.*", line)
            assert match, f"{path.name}: a trigger line not read: {line!r}"
            keys.add(match.group(1))
    assert keys, f"{path.name}: no trigger read"
    return keys


def _job_conditions(block: str) -> list[str]:
    """Each job-level `if:` of a job's block, with any lines it continues onto."""
    lines = block.splitlines()
    conditions = []
    for at, line in enumerate(lines):
        if line.startswith("    if:"):
            value = [line.strip()]
            for following in lines[at + 1 :]:
                if following.strip() and len(following) - len(following.lstrip()) <= 4:
                    break
                value.append(following.strip())
            conditions.append(" ".join(part for part in value if part))
    return conditions


def test_every_workflow_a_release_run_starts_from_is_checked_below():
    """A release workflow that something other than another workflow can start is
    an entry, and an entry missing from the list would go unchecked. Found by
    name, as the action pins are, so a new one fails here until it is listed."""
    entries = {path.name for path in _release_workflows() if _triggers(path) - {"workflow_call"}}
    assert entries == set(_ENTRIES), sorted(entries)


@pytest.mark.parametrize(
    ("workflow", "entry", "environment"), [(name, *entry) for name, entry in _ENTRIES.items()]
)
def test_only_main_here_starts_a_release_run(workflow, entry, environment):
    """A run from anywhere but this repository's main is skipped at its entry job,
    rather than failing later where it reaches the release environment or its key.

    So the entry's condition is held as the whole line: one widened around it, by
    an `||` or a negation, fails here as surely as one with a half missing. Every
    other job of the run waits on the entry and uses no status function the set
    above does not hold, read with the lines a condition continues onto, so none
    runs once the entry is skipped; and no job of this file but the entry names
    an environment at all, so the release environment is otherwise reached only
    through the workflows these jobs call, and so only after the entry.
    """
    path = WORKFLOWS / workflow
    conditions = [
        line.strip() for line in job(path, entry).splitlines() if line.startswith("    if:")
    ]
    assert conditions == [_GUARD], conditions

    section = wiring(path).split("\njobs:\n", 1)[1]
    keys = [line for line in section.splitlines() if re.match(r"  \S", line)]
    unread = [line for line in keys if not re.fullmatch(r"  [\w-]+:", line)]
    assert unread == [], unread
    jobs = [line.strip()[:-1] for line in keys]

    with_environment = [
        name for name in jobs if re.search(r"^    environment:", job(path, name), re.M)
    ]
    assert with_environment == ([entry] if environment else []), with_environment
    assert not environment or _ENVIRONMENT.search(job(path, entry)), job(path, entry)

    past_skips = [
        condition
        for name in jobs
        if name != entry
        for condition in _job_conditions(job(path, name))
        if _STATUS.search(condition) and condition not in _STATUS_CONDITIONS
    ]
    assert past_skips == [], past_skips

    def waits_on(name: str) -> set[str]:
        lines = [line for line in job(path, name).splitlines() if line.startswith("    needs:")]
        return set(re.findall(r"[\w-]+", lines[0].partition(":")[2])) if lines else set()

    reached = {entry}
    while more := {name for name in jobs if waits_on(name) & reached} - reached:
        reached |= more
    assert reached == set(jobs), sorted(set(jobs) - reached)


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


def test_every_run_rebuilds_each_open_release_pull_request():
    """Both release pull requests edit the manifest, so merging one leaves the other
    in conflict until it is rebuilt from main.

    By default release-please rewrites an open release pull request only when its
    description would change, and a release of the other component does not change
    it. The branch is the release App's alone, so nobody else can resolve the
    conflict.
    """
    assert _config().get("always-update") is True


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
