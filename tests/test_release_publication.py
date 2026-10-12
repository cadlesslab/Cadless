"""What a release carries reaches it only through its checks, and only from main.

Publishing runs on GitHub alone, after a release pull request is merged, so a
slip in it would show only on the first release after it; only the build also
runs on every pull request, as a dry run. Where a step's own shell decides
something, its script is taken out of the workflow and run here; where the
wiring decides, the wiring is read, comments excluded.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

import cadless
from tests.workflow_text import job, step_script, wiring

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github/workflows"
ARTIFACTS = WORKFLOWS / "release-artifacts.yml"
IMAGES = WORKFLOWS / "release-images.yml"
ATTACH = WORKFLOWS / "release-attach.yml"
BACKFILL = WORKFLOWS / "release-backfill.yml"
RELEASE = WORKFLOWS / "release.yml"
CI = WORKFLOWS / "ci.yml"


def _bash(script: str, tmp_path: Path, **env: str) -> subprocess.CompletedProcess[str]:
    """Run a step's script the way the runner's `bash -e` does, with `bin/` first on PATH."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    python = bin_dir / "python"
    if not python.exists():
        python.symlink_to(sys.executable)
    full_env = dict(os.environ, PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}", **env)
    return subprocess.run(
        ["bash", "-eo", "pipefail", "-c", script],
        cwd=env.get("CWD", ROOT),
        env=full_env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _fake(tmp_path: Path, name: str, body: str) -> None:
    tool = tmp_path / "bin" / name
    tool.parent.mkdir(exist_ok=True)
    tool.write_text("#!/bin/bash\n" + body, encoding="utf-8")
    tool.chmod(0o755)


# The build ------------------------------------------------------------------


def test_the_build_cannot_publish():
    """Pull requests from anyone run it, so it holds nothing that writes."""
    text = wiring(ARTIFACTS)
    assert "permissions:\n  contents: read" in text
    assert "write" not in text
    assert "gh release" not in text and "docker push" not in text


def test_the_build_installs_its_tools_by_hash():
    assert "pip install --require-hashes" in step_script(
        ARTIFACTS, "Build the sdist, and the wheel from it"
    )
    pins = (ROOT / "tools/release-build-requirements.txt").read_text(encoding="utf-8")
    requirements = [line for line in pins.splitlines() if line and not line.startswith(("#", " "))]
    assert requirements and all("==" in line and line.endswith("\\") for line in requirements)


def test_the_build_fails_when_the_scan_finds_something(tmp_path):
    """The step's own exit status is what stops the upload that follows it."""
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "notes.txt").write_text(f"see {_planted()}\n", encoding="utf-8")
    script = step_script(ARTIFACTS, "No internal references found in what the release carries")
    script = script.replace("tools/release_artifacts.py", str(ROOT / "tools/release_artifacts.py"))
    assert _bash(script, tmp_path, CWD=str(tmp_path)).returncode != 0


def _planted() -> str:
    from tools import release_artifacts

    return release_artifacts._PLANTED


# The tag ----------------------------------------------------------------------


TAG_STEPS = [
    (IMAGES, "The tag is a backend release tag"),
    (ATTACH, "The tag is a release tag"),
    (BACKFILL, "The tag is a release tag"),
]


@pytest.mark.parametrize(("workflow", "step"), TAG_STEPS)
@pytest.mark.parametrize(
    "tag",
    ["their-branch", "refs/pull/1/merge", "backend-v1.2", "backend-v1.2.3; true", "v1.0.0"],
)
def test_a_tag_input_that_is_not_a_release_tag_is_refused(tmp_path, workflow, step, tag):
    """The input is text anyone dispatching may type; it is read before any checkout."""
    result = _bash(
        step_script(workflow, step), tmp_path, TAG=tag, GITHUB_OUTPUT=str(tmp_path / "out")
    )
    assert result.returncode != 0


@pytest.mark.parametrize(("workflow", "step"), TAG_STEPS)
def test_a_release_tag_input_is_accepted(tmp_path, workflow, step):
    result = _bash(
        step_script(workflow, step),
        tmp_path,
        TAG="backend-v1.2.3",
        GITHUB_OUTPUT=str(tmp_path / "out"),
    )
    assert result.returncode == 0


@pytest.mark.parametrize("workflow", [IMAGES, ATTACH])
def test_the_tag_is_checked_before_the_repository_is_checked_out(workflow):
    text = wiring(workflow)
    assert text.index("release tag\n") < text.index("actions/checkout")


@pytest.mark.parametrize("workflow", [IMAGES, ATTACH])
def test_a_tag_is_checked_out_as_a_tag_on_main(workflow):
    """A bare name checks out a branch of the same name before a tag."""
    text = wiring(workflow)
    assert "ref: refs/tags/${{ inputs.tag }}" in text
    assert "fetch-depth: 0" in text, "the check below needs main's history"
    assert text.index("git merge-base --is-ancestor HEAD origin/main") < text.index(
        "release_artifacts.py"
    )


@pytest.mark.parametrize("workflow", [IMAGES, ATTACH])
def test_a_tagged_commit_off_main_stops_the_job(tmp_path, workflow):
    _fake(tmp_path, "git", "exit 1\n")
    assert _bash(step_script(workflow, "The tagged commit is on main"), tmp_path).returncode != 0


@pytest.mark.parametrize("workflow", [IMAGES, ATTACH])
def test_publishing_runs_in_the_release_environment(workflow):
    """Its branch rule is what keeps a run from any branch but main out."""
    assert "environment: release" in wiring(workflow)


@pytest.mark.parametrize(
    ("workflow", "step"),
    [
        (IMAGES, "The tag names the version its tree carries"),
        (ATTACH, "The tag names the version its tree carries"),
    ],
)
def test_a_tag_naming_another_version_fails_its_step(tmp_path, workflow, step):
    out = tmp_path / "out"
    result = _bash(
        step_script(workflow, step), tmp_path, TAG="backend-v9.9.9", GITHUB_OUTPUT=str(out)
    )
    assert result.returncode != 0


def test_the_images_refuse_a_frontend_release(tmp_path):
    """A frontend tag that matches its own tree is still not one images are pushed for."""
    tag = f"frontend-v{(ROOT / 'frontend/version.txt').read_text(encoding='utf-8').strip()}"
    script = step_script(IMAGES, "The tag names the version its tree carries")
    assert _bash(script, tmp_path, TAG=tag).returncode != 0


def test_a_matching_tag_names_its_component(tmp_path):
    out = tmp_path / "out"
    script = step_script(ATTACH, "The tag names the version its tree carries")
    result = _bash(script, tmp_path, TAG=f"backend-v{cadless.__version__}", GITHUB_OUTPUT=str(out))
    assert result.returncode == 0, result.stderr
    assert out.read_text(encoding="utf-8") == "component=backend\n"


# Images and attachments -------------------------------------------------------


HEAD = "0123456789abcdef0123456789abcdef01234567"


def _registry(
    tmp_path: Path, existing: str, built_from: str = HEAD, refusal: str = "not found"
) -> Path:
    """Stand-ins for docker, jq and git, and the docker calls they saw.

    The fake registry holds `existing` from the start, labelled as built from
    `built_from`, and any version once it is pushed; for anything else it
    answers with `refusal`. Every image has the same digest.
    """
    log = tmp_path / "docker.log"
    _fake(
        tmp_path,
        "docker",
        f'echo "$*" >> "{log}"\n'
        'if [ "$1 $2 $3" = "buildx imagetools inspect" ]; then\n'
        f'  case "$4" in *{existing}:*) ;; *)\n'
        f'    grep -qxF "push $4" "{log}" || {{ echo "ERROR: $4: {refusal}" >&2; exit 1; }} ;;\n'
        "  esac\n"
        '  case "$6" in\n'
        f'    *Image*) echo \'{{"config": {{"Labels": {{"org.opencontainers.image.revision": "{built_from}"}}}}}}\' ;;\n'
        '    *Manifest*) echo \'{"digest": "sha256:feed"}\' ;;\n'
        "  esac\n"
        "  exit 0\n"
        "fi\n",
    )
    # Enough of jq for the two filters the step uses: an image's revision label
    # and a manifest's digest.
    _fake(
        tmp_path,
        "jq",
        'exec python -c "\n'
        "import json, sys\n"
        "data = json.load(sys.stdin)\n"
        "if 'digest' in sys.argv[-1]:\n"
        "    print(data['digest'])\n"
        "else:\n"
        "    image = data if 'config' in data else data['linux/amd64']\n"
        "    print(image['config']['Labels'].get('org.opencontainers.image.revision', ''))\n"
        '" "$@"\n',
    )
    _fake(tmp_path, "git", f"echo {HEAD}\n")
    return log


def _push_images(tmp_path: Path) -> subprocess.CompletedProcess[str]:
    script = step_script(IMAGES, "Push the API and worker images")
    env = {
        "TAG": "backend-v1.2.3",
        "GH_TOKEN": "t",
        "ACTOR": "a",
        "OWNER": "o",
        "REPOSITORY": "o/r",
    }
    return _bash(script, tmp_path, CWD=str(tmp_path), **env)


def _pushes(log: Path) -> list[str]:
    return [c for c in log.read_text(encoding="utf-8").splitlines() if c.startswith("push ")]


def test_an_image_version_already_pushed_from_this_commit_is_kept(tmp_path):
    log = _registry(tmp_path, existing="cadless-api")
    result = _push_images(tmp_path)
    assert result.returncode == 0, result.stderr
    assert _pushes(log) == ["push ghcr.io/o/cadless-worker:1.2.3"]


def test_an_image_version_pushed_from_another_commit_stops_the_run(tmp_path):
    """Whoever pushed the version first must not get it vouched for by the release."""
    log = _registry(tmp_path, existing="cadless-api", built_from="f" * 40)
    result = _push_images(tmp_path)
    assert result.returncode != 0
    assert _pushes(log) == []


def test_a_registry_that_cannot_answer_stops_the_run(tmp_path):
    """A failed lookup is not an absent version: pushing then could replace one."""
    log = _registry(tmp_path, existing="nothing", refusal="unexpected status: 502 Bad Gateway")
    result = _push_images(tmp_path)
    assert result.returncode != 0
    assert _pushes(log) == []


def test_every_image_is_recorded_by_its_digest(tmp_path):
    _registry(tmp_path, existing="cadless-api")
    assert _push_images(tmp_path).returncode == 0
    assert (tmp_path / "cadless-1.2.3-images.txt").read_text(encoding="utf-8").splitlines() == [
        "ghcr.io/o/cadless-api:1.2.3@sha256:feed",
        "ghcr.io/o/cadless-worker:1.2.3@sha256:feed",
    ]


def test_the_images_carry_the_release_version(tmp_path):
    log = _registry(tmp_path, existing="nothing-yet")
    assert _push_images(tmp_path).returncode == 0
    builds = [c for c in log.read_text(encoding="utf-8").splitlines() if c.startswith("build ")]
    assert len(builds) == 2
    assert all("org.opencontainers.image.version=1.2.3" in b for b in builds)


def _attach(
    tmp_path: Path, attached_bytes: bytes
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    """Run the attach step against a draft release already holding the sdist as `attached_bytes`.

    The fake answers only the release list, as GitHub does for a draft: the
    lookup by tag knows only published releases.
    """
    log = tmp_path / "gh.log"
    digest = hashlib.sha256(attached_bytes).hexdigest()
    _fake(
        tmp_path,
        "gh",
        f'echo "$*" >> "{log}"\n'
        'if [ "$1" = api ] && [[ "$*" == *"repos/o/r/releases --jq"* ]]; then\n'
        f'  echo "cadless-1.2.3.tar.gz sha256:{digest}"\n'
        "fi\n",
    )
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "cadless-1.2.3.tar.gz").write_bytes(b"sdist")
    (dist / "cadless-1.2.3-py3-none-any.whl").write_bytes(b"wheel")
    script = step_script(ATTACH, "Attach what is not attached yet")
    env = {"TAG": "backend-v1.2.3", "GH_TOKEN": "t", "REPOSITORY": "o/r"}
    result = _bash(script, tmp_path, CWD=str(tmp_path), **env)
    uploads = [
        c for c in log.read_text(encoding="utf-8").splitlines() if c.startswith("release upload")
    ]
    return result, uploads


def test_only_what_is_missing_is_attached(tmp_path):
    result, uploads = _attach(tmp_path, attached_bytes=b"sdist")
    assert result.returncode == 0, result.stderr
    assert uploads == [
        "release upload backend-v1.2.3 dist/cadless-1.2.3-py3-none-any.whl --repo o/r"
    ]


def test_an_attached_asset_with_other_bytes_stops_the_run_and_stays(tmp_path):
    result, uploads = _attach(tmp_path, attached_bytes=b"something else")
    assert result.returncode != 0
    assert "other bytes" in result.stdout
    assert all("cadless-1.2.3.tar.gz" not in upload for upload in uploads)


def test_files_for_another_version_stop_the_job(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "cadless-frontend-0.0.1.tar.gz").write_text("x", encoding="utf-8")
    script = step_script(ATTACH, "The files are the ones this release carries")
    script = script.replace("tools/release_artifacts.py", str(ROOT / "tools/release_artifacts.py"))
    assert _bash(script, tmp_path, CWD=str(tmp_path), TAG="frontend-v4.5.6").returncode != 0


def test_what_is_attached_is_verified_and_scanned_first():
    text = wiring(ATTACH)
    upload = text.index("gh release upload")
    assert text.index("release_artifacts.py verify") < upload
    assert text.index("release_artifacts.py scan dist") < upload


def test_the_release_is_published_once_everything_is_attached(tmp_path):
    """A failed step ends the job, so a step after the attaching one runs only once it passed."""
    text = wiring(ATTACH)
    assert text.index("gh release upload") < text.index("- name: Publish the release")
    log = tmp_path / "gh.log"
    _fake(tmp_path, "gh", f'echo "$*" >> "{log}"\n')
    script = step_script(ATTACH, "Publish the release")
    env = {"TAG": "backend-v1.2.3", "GH_TOKEN": "t", "REPOSITORY": "o/r"}
    assert _bash(script, tmp_path, **env).returncode == 0
    assert log.read_text(encoding="utf-8").splitlines() == [
        "release edit backend-v1.2.3 --draft=false --repo o/r"
    ]


# The wiring -------------------------------------------------------------------


def test_release_please_names_the_tag_of_each_release_it_created():
    outputs = job(RELEASE, "release-please")
    assert (
        "steps.release.outputs.release_created == 'true' && steps.release.outputs.tag_name"
        in outputs
    )
    assert (
        "steps.release.outputs['frontend--release_created'] == 'true'"
        " && steps.release.outputs['frontend--tag_name']"
    ) in outputs


@pytest.mark.parametrize("component", ["backend", "frontend"])
def test_a_release_is_built_from_its_tag_only_when_one_was_created(component):
    build = job(RELEASE, f"{component}-artifacts")
    assert f"if: needs.release-please.outputs.{component} != ''" in build
    assert "uses: ./.github/workflows/release-artifacts.yml" in build
    assert f"ref: refs/tags/${{{{ needs.release-please.outputs.{component} }}}}" in build


def test_the_backend_pushes_its_images_after_the_build_and_before_attaching():
    assert "needs: [release-please, backend-artifacts]" in job(RELEASE, "backend-images")
    assert "needs: [release-please, backend-images]" in job(RELEASE, "backend-attach")


def test_the_frontend_attaches_after_its_build_and_never_writes_packages():
    attach = job(RELEASE, "frontend-attach")
    assert "needs: [release-please, frontend-artifacts]" in attach
    assert "packages" not in attach


# A caller grants a called workflow's job no more than it states itself, and a
# call that grants less than the job asks for fails the whole run at startup —
# release pull requests included — so each grant is held here.
CALLER_GRANTS = [
    (RELEASE, "backend-images", "permissions:\n      contents: read\n      packages: write\n"),
    (RELEASE, "backend-attach", "permissions:\n      contents: write\n"),
    (RELEASE, "frontend-attach", "permissions:\n      contents: write\n"),
    (BACKFILL, "images", "permissions:\n      contents: read\n      packages: write\n"),
    (BACKFILL, "attach", "permissions:\n      contents: write\n"),
]


@pytest.mark.parametrize(("workflow", "name", "grant"), CALLER_GRANTS)
def test_every_publishing_call_grants_what_its_job_asks_for(workflow, name, grant):
    assert grant in job(workflow, name)


def test_a_backfill_is_dispatched_from_main_only():
    text = wiring(BACKFILL)
    assert "workflow_dispatch:" in text and "\n  push:" not in text
    resolve = job(BACKFILL, "resolve")
    assert "github.ref == 'refs/heads/main'" in resolve
    assert "actions/checkout" not in resolve, "the dispatched input is checked before any code runs"


def test_a_backfill_builds_the_tag_and_pushes_images_for_the_backend_only():
    assert "ref: refs/tags/${{ inputs.tag }}" in job(BACKFILL, "artifacts")
    assert "if: needs.resolve.outputs.component == 'backend'" in job(BACKFILL, "images")


def test_a_backfill_publishes_nothing_its_build_did_not_pass():
    assert "needs: [resolve, artifacts]" in job(BACKFILL, "images")
    attach = job(BACKFILL, "attach")
    assert "needs: [artifacts, images]" in attach
    assert "needs.artifacts.result == 'success'" in attach


def test_a_backfill_never_runs_beside_a_release_run():
    assert "concurrency:\n  group: release\n  cancel-in-progress: false" in wiring(BACKFILL)


@pytest.mark.parametrize("component", ["backend", "frontend"])
def test_every_pull_request_dry_runs_each_release(component):
    dry_run = job(CI, f"release-artifacts-{component}")
    assert "uses: ./.github/workflows/release-artifacts.yml" in dry_run
    assert f"component: {component}" in dry_run
    assert "ref: ${{ github.sha }}" in dry_run, "the dry run builds the pull request's own commit"


def test_the_sdist_leaves_the_separately_released_trees_out():
    """The default sdist takes the whole tree; the catalog and the frontend ship on their own."""
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    included = pyproject["tool"]["hatch"]["build"]["targets"]["sdist"]["only-include"]
    assert "catalog" not in included and "frontend" not in included
