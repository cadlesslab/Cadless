"""What a release carries reaches it only through its checks, and only from main.

Publishing runs on GitHub alone, after a release pull request is merged, so a
slip in it would show only on the first release after it; only the build also
runs on every pull request, as a dry run. Where a step's own shell decides
something, its script is taken out of the workflow and run here; where the
wiring decides, the wiring is read, comments excluded.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tomllib
from pathlib import Path
from typing import NamedTuple

import pytest

import cadless
from tests.git_env import isolated_git_env
from tests.workflow_text import (
    job,
    step_env,
    step_id,
    step_keys,
    step_names,
    step_script,
    wiring,
)

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
    full_env = isolated_git_env(PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}", **env)
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
    assert "gh release" not in text and "gh api" not in text and "docker push" not in text


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


def test_the_frontend_release_is_built_into_its_image_from_itself_alone(tmp_path):
    """The build context is the unpacked asset and nothing else, so a file the image
    needs from elsewhere in the repository fails here as it would for whoever takes
    the release; and the prefix is not the default, so the stage rewriting the
    server's config for it runs."""
    dist = tmp_path / "dist"
    dist.mkdir()
    with tarfile.open(dist / "cadless-frontend-1.2.3.tar.gz", "w:gz") as archive:
        for name, data in {
            "frontend/Dockerfile": b"FROM x\n",
            "frontend/nginx.conf": b"{}\n",
        }.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    log = tmp_path / "docker.log"
    _fake(tmp_path, "docker", f'echo "$*" >> "{log}"\nls "${{@: -1}}" >> "{log}"\n')
    script = step_script(ARTIFACTS, "The frontend release builds its image from itself alone")
    assert _bash(script, tmp_path, CWD=str(tmp_path)).returncode == 0
    build, *context_holds = log.read_text(encoding="utf-8").splitlines()
    context = build.split()[-1]
    assert build.startswith(f"build -f {context}/frontend/Dockerfile ")
    assert "--build-arg BASE_PATH=/apps/release-check/" in build
    assert context_holds == ["frontend"]


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
    names = step_names(workflow)
    assert names.index("The tagged commit is on main") < names.index(
        "The tag names the version its tree carries"
    )


def _git(repo: Path, *args: str) -> str:
    """Git in `repo` under a throwaway identity, with no signing asked of it."""
    return subprocess.run(
        ["git", "-c", "user.name=r", "-c", "user.email=r@example.com"]
        + ["-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false", *args],
        cwd=repo,
        env=isolated_git_env(),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _checkout(tmp_path: Path, on_main: bool) -> Path:
    """A tag checked out the way actions/checkout leaves it: main fetched as
    origin/main, every tag fetched too, and HEAD detached at the tagged commit.

    On main, main has moved on past that commit. Off main, the commit also
    carries a tag named origin/main, which a bare `origin/main` resolves to
    before the remote-tracking branch.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "first")
    first = _git(repo, "rev-parse", "HEAD")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "second")
    second = _git(repo, "rev-parse", "HEAD")
    if on_main:
        _git(repo, "update-ref", "refs/remotes/origin/main", second)
    else:
        _git(repo, "update-ref", "refs/remotes/origin/main", first)
        _git(repo, "tag", "origin/main", second)
    _git(repo, "switch", "-q", "--detach", first if on_main else second)
    return repo


@pytest.mark.parametrize("workflow", [IMAGES, ATTACH])
def test_a_tagged_commit_off_main_stops_the_job(tmp_path, workflow):
    repo = _checkout(tmp_path, on_main=False)
    script = step_script(workflow, "The tagged commit is on main")
    assert _bash(script, tmp_path, CWD=str(repo)).returncode != 0


@pytest.mark.parametrize("workflow", [IMAGES, ATTACH])
def test_a_tagged_commit_on_main_passes(tmp_path, workflow):
    repo = _checkout(tmp_path, on_main=True)
    result = _bash(step_script(workflow, "The tagged commit is on main"), tmp_path, CWD=str(repo))
    assert result.returncode == 0, result.stderr


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


def test_files_for_another_version_stop_the_job(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "cadless-frontend-0.0.1.tar.gz").write_text("x", encoding="utf-8")
    script = step_script(ATTACH, "The files are the ones this release carries")
    script = script.replace("tools/release_artifacts.py", str(ROOT / "tools/release_artifacts.py"))
    assert _bash(script, tmp_path, CWD=str(tmp_path), TAG="frontend-v4.5.6").returncode != 0


def test_what_is_attached_is_verified_and_scanned_first():
    names = step_names(ATTACH)
    attach = names.index("Attach what is not attached yet")
    assert names.index("The files are the ones this release carries") < attach
    assert names.index("No internal references found in what is attached") < attach


# The release itself -----------------------------------------------------------


FAKE_GITHUB = ROOT / "tests/fake_github.py"
SDIST = "cadless-1.2.3.tar.gz"
WHEEL = "cadless-1.2.3-py3-none-any.whl"
BUILT = {SDIST: b"sdist", WHEEL: b"wheel"}

# The fake answers gh's --jq through the real jq, which every GitHub runner
# carries; only a machine without it skips these, and CI never does.
needs_jq = pytest.mark.skipif(
    shutil.which("jq") is None and not os.environ.get("CI"), reason="jq is not installed"
)


def _release(
    release_id: int,
    *,
    tag: str = "backend-v1.2.3",
    draft: bool = True,
    assets: dict[str, bytes] | None = None,
) -> dict:
    return {
        "id": release_id,
        "tag_name": tag,
        "draft": draft,
        "assets": [
            {"name": name, "digest": f"sha256:{hashlib.sha256(data).hexdigest()}"}
            for name, data in (assets or {}).items()
        ],
    }


class _Run(NamedTuple):
    failed: str | None
    said: str
    releases: list[dict]
    calls: list[str]


def _resolve(value: str, context: dict[str, str]) -> str:
    """An env value with each `${{ … }}` read from `context`; one it lacks is an error."""
    return re.sub(r"\$\{\{ *([\w.-]+) *\}\}", lambda found: context[found[1]], value)


def _publish(tmp_path: Path, releases: list[dict], stored_bytes: bytes | None = None) -> _Run:
    """Run the steps after the scan, as the runner does, with BUILT downloaded into dist.

    The steps and their order are read from the job. Each step gets the env its
    own `env:` names, the outputs of the steps before it included, and the run
    stops at the first step that fails, keeping what that step said. With
    `stored_bytes`, GitHub keeps every upload as those bytes instead.
    """
    state = tmp_path / "github"
    state.mkdir()
    (state / "releases.json").write_text(json.dumps(releases), encoding="utf-8")
    (state / "calls.log").touch()
    (state / "token").write_text("t", encoding="utf-8")
    if stored_bytes is not None:
        (state / "stored-bytes").write_bytes(stored_bytes)
    _fake(tmp_path, "gh", f'exec "{sys.executable}" "{FAKE_GITHUB}" "{state}" "$@"\n')
    dist = tmp_path / "dist"
    dist.mkdir()
    for name, data in BUILT.items():
        (dist / name).write_bytes(data)
    names = step_names(ATTACH)
    steps = names[names.index("No internal references found in what is attached") + 1 :]
    # A key this loop does not act on, `if:` or `continue-on-error:`, would let the
    # runner go on where this stops — on any of the steps, including those it never
    # reaches.
    for name in steps:
        assert step_keys(ATTACH, name) <= {"id", "env", "run"}, name
    context = {"github.token": "t", "github.repository": "o/r", "inputs.tag": "backend-v1.2.3"}
    failed, said = None, ""
    for name in steps:
        output = tmp_path / "output"
        output.write_text("", encoding="utf-8")
        env = {key: _resolve(value, context) for key, value in step_env(ATTACH, name).items()}
        script = step_script(ATTACH, name)
        result = _bash(script, tmp_path, CWD=str(tmp_path), GITHUB_OUTPUT=str(output), **env)
        if result.returncode != 0:
            failed, said = name, result.stdout + result.stderr
            break
        for line in output.read_text(encoding="utf-8").splitlines():
            key, equals, value = line.partition("=")
            assert equals and "<<" not in key, f"{name} wrote an output not read here: {line!r}"
            context[f"steps.{step_id(ATTACH, name)}.outputs.{key}"] = value
    return _Run(
        failed,
        said,
        json.loads((state / "releases.json").read_text(encoding="utf-8")),
        (state / "calls.log").read_text(encoding="utf-8").splitlines(),
    )


@needs_jq
def test_only_what_is_missing_is_attached(tmp_path):
    run = _publish(
        tmp_path, [_release(3, tag="frontend-v1.2.3"), _release(7, assets={SDIST: b"sdist"})]
    )
    assert run.failed is None
    uploads = [call for call in run.calls if call.startswith("api --method POST")]
    assert len(uploads) == 1
    assert uploads[0].endswith(f"/repos/o/r/releases/7/assets?name={WHEEL}")


@needs_jq
def test_a_release_that_holds_what_was_built_is_published(tmp_path):
    run = _publish(
        tmp_path, [_release(3, tag="frontend-v1.2.3"), _release(7, assets={SDIST: b"sdist"})]
    )
    assert [release["draft"] for release in run.releases] == [True, False]


@needs_jq
def test_an_attached_asset_with_other_bytes_stops_the_run_and_stays(tmp_path):
    run = _publish(tmp_path, [_release(7, assets={SDIST: b"something else"})])
    assert run.failed == "Attach what is not attached yet"
    assert "is attached with other bytes" in run.said
    changes = [call for call in run.calls if "--method" in call]
    assert not [call for call in changes if SDIST in call or "DELETE" in call]


@needs_jq
def test_an_asset_with_no_digest_is_named_for_what_it_is(tmp_path):
    """Not "other bytes": there are none to compare, and the maintainer looks elsewhere."""
    release = _release(7, assets={SDIST: b"sdist"})
    release["assets"].append({"name": WHEEL, "digest": None})
    run = _publish(tmp_path, [release])
    assert run.failed == "Attach what is not attached yet"
    assert "no digest" in run.said


@needs_jq
@pytest.mark.parametrize(
    "releases",
    [[], [_release(7), _release(8, draft=False)]],
    ids=["no release", "two releases"],
)
def test_a_tag_that_names_no_release_or_two_stops_the_job(tmp_path, releases):
    run = _publish(tmp_path, releases)
    assert run.failed == "Find the release"
    assert "expected exactly one release" in run.said


@needs_jq
def test_an_asset_nobody_built_keeps_the_release_a_draft(tmp_path):
    run = _publish(tmp_path, [_release(7, assets={**BUILT, "notes.txt": b"added by hand"})])
    assert run.releases[0]["draft"] is True
    assert run.failed == "The release holds exactly what was built"
    assert "does not hold exactly what was built" in run.said


@needs_jq
def test_an_upload_kept_as_other_bytes_keeps_the_release_a_draft(tmp_path):
    """Every name matches, so only the digests can tell."""
    run = _publish(tmp_path, [_release(7, assets={SDIST: b"sdist"})], stored_bytes=b"other")
    assert run.releases[0]["draft"] is True
    assert run.failed == "The release holds exactly what was built"
    assert "does not hold exactly what was built" in run.said


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
