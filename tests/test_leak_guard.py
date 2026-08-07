"""Tests for the repository leak guard (tools/leak_guard.py).

Violation samples are assembled from fragments so this file itself stays
clean under the guard's own scan.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_GUARD = _ROOT / "tools" / "leak_guard.py"


def _load():
    spec = importlib.util.spec_from_file_location("leak_guard", _GUARD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


leak_guard = _load()

INTERNAL_SAMPLES = {
    "ticket": "VT3-" + "1234",
    "tracker key": "INNOL-" + "83",
    "tracker key lowercased": "innol-" + "9",
    "design-docs path": "see " + "plan" + "-docs" + "/500-public-release.md",
    "dev path": "/home/" + "ubuntu" + "/data/catalog",
    "infra host": "https://" + "fold" + "less" + ".ai/page",
    "internal domain": "https://" + "inno" + "lingua" + ".ai",
    "platform endpoint": "https://api." + "cad" + "less" + ".ai/v1/packages",
    "platform endpoint bare": "https://" + "cad" + "less" + ".ai/pricing",
    "platform endpoint with a port": "connect to " + "cad" + "less" + ".ai:443",
    "platform settings key": "CADLESS_" + "MARKET" + "_API_BASE",
    # The hosted half spells this one lowercased on the field it declares, so a
    # case-sensitive pattern would miss the form most likely to be copied back.
    "platform settings key lowercased": "    " + "market" + "_api_base" + ": str = ...",
    "platform namespace underscored": "from " + "cad" + "less" + "_market.client import X",
    "platform namespace hyphenated": "pip install " + "cad" + "less" + "-market",
    # The other repository's own name. A reader needs the shape, not the name.
    "platform repository name": "see the " + "cad" + "less" + "-platform repo",
    "platform repository underscored": "import " + "cad" + "less" + "_platform",
    "platform authoring directory": "run " + "catalog_" + "authoring" + "/bake.py",
    "platform plugin directory": "vendored from " + "engine_" + "plugin" + "/panel",
}

SECRET_SAMPLES = {
    "anthropic key": "sk-ant-" + "a" * 24,
    "aws key": "AKIA" + "A" * 16,
    "github token": "ghp_" + "a" * 36,
    "slack token": "xoxb-" + "123456789012",
    "private key block": "-----BEGIN " + "PRIVATE KEY-----",
}


def test_clean_tree_passes(tmp_path):
    (tmp_path / "main.py").write_text("print('a clean build123d file')\n")
    assert leak_guard.scan_tree(tmp_path) == []
    assert leak_guard.run(tmp_path) is True


@pytest.mark.parametrize("sample", list(INTERNAL_SAMPLES.values()), ids=list(INTERNAL_SAMPLES))
def test_detects_internal_references(tmp_path, sample):
    (tmp_path / "doc.md").write_text(f"prose\n{sample}\nmore prose\n")
    findings = leak_guard.scan_tree(tmp_path)
    assert findings and all("doc.md" in finding for finding in findings)
    assert leak_guard.run(tmp_path) is False


@pytest.mark.parametrize("sample", list(SECRET_SAMPLES.values()), ids=list(SECRET_SAMPLES))
def test_detects_credential_shapes(tmp_path, sample):
    (tmp_path / "config.py").write_text(f"TOKEN = '{sample}'\n")
    assert leak_guard.scan_tree(tmp_path)
    assert leak_guard.run(tmp_path) is False


def test_project_name_and_clone_url_are_not_platform_endpoints(tmp_path):
    # The platform patterns name a hosted service, not this project. The
    # repository's own name, its clone URL and the package name all share the
    # prefix — matching those would fire on CONTRIBUTING.md and the README
    # forever, and this guard has no allowlist to except them with.
    (tmp_path / "CONTRIBUTING.md").write_text(
        "git clone https://github.com/cadlesslab/Cadless.git\n"
        "The Python package is `cadless` and config uses the CADLESS_ prefix.\n"
        "Open http://localhost:8800 to browse the bundled catalog.\n"
        "The marketplace and the catalog are described in the extension guides.\n"
        "A hosted platform exists; this repository does not contain it.\n"
    )
    assert leak_guard.run(tmp_path) is True


def test_the_projects_own_dotted_namespace_is_not_a_hosted_endpoint(tmp_path):
    # The domain pattern ends on a word boundary. Without it, any module of this
    # project's own whose name merely starts with those two letters would read as
    # the hosted endpoint — and with no allowlist, the only remedy would be
    # editing the guard on the day such a module is legitimately added.
    # Assembled from fragments like every other sample here: spelled out, these
    # two would carry the hosted domain as a substring, and a coarse audit grep
    # over the published tree would flag this file forever.
    ns = "cad" + "less" + "."
    (tmp_path / "importer.py").write_text(
        f"from {ns}airflow_adapter import schedule\n# see also {ns}aiff for fixtures\n"
    )
    assert leak_guard.run(tmp_path) is True


def test_skips_git_internals_and_binaries(tmp_path):
    hidden = tmp_path / ".git"
    hidden.mkdir()
    (hidden / "config").write_text("VT3-" + "9999\n")
    (tmp_path / "mesh.glb").write_bytes(b"VT3-" + b"9999")
    assert leak_guard.run(tmp_path) is True


def test_skips_git_pointer_file_in_a_linked_worktree(tmp_path):
    # In a linked worktree .git is a file holding an absolute gitdir path, not
    # a directory, so pruning by directory name alone never reaches it.
    (tmp_path / ".git").write_text("gitdir: /home/" + "ubuntu" + "/repo/.git/worktrees/wt\n")
    assert leak_guard.run(tmp_path) is True


def test_scans_ascii_cad_exports(tmp_path):
    # STEP and OBJ are ASCII, not binary: a STEP FILE_NAME header carries an
    # author and organisation, and the OBJ header carries the exporter's own
    # banner. Skipping them by suffix would hand the guard a blind spot in the
    # part of the tree most likely to quote a machine or a person.
    (tmp_path / "model.obj").write_text("# exported by\nVT3-" + "1234\n")
    (tmp_path / "model.step").write_text("FILE_NAME('/home/" + "ubuntu" + "/a.step');\n")
    findings = leak_guard.scan_tree(tmp_path)
    assert {finding.split("] ", 1)[1].split(":")[0] for finding in findings} == {
        "model.obj",
        "model.step",
    }


def test_scans_a_file_named_like_a_skipped_directory(tmp_path):
    # Only .git is skipped by name. A file called build or dist is ordinary
    # content — skipping it would quietly shrink the guard's reach.
    (tmp_path / "build").write_text("VT3-" + "1234\n")
    assert leak_guard.run(tmp_path) is False


def test_fail_closed_on_unexpected_error(tmp_path, monkeypatch):
    def boom(_root):
        raise RuntimeError("disk fell off")

    monkeypatch.setattr(leak_guard, "scan_tree", boom)
    assert leak_guard.run(tmp_path) is False


def test_cli_exit_codes(tmp_path):
    clean = tmp_path / "clean"
    clean.mkdir()
    (clean / "ok.txt").write_text("fine\n")
    dirty = tmp_path / "dirty"
    dirty.mkdir()
    (dirty / "bad.txt").write_text("VT3-" + "1234\n")
    assert subprocess.run([sys.executable, str(_GUARD), str(clean)]).returncode == 0
    assert subprocess.run([sys.executable, str(_GUARD), str(dirty)]).returncode == 1


def test_repository_tree_is_clean():
    assert leak_guard.run(_ROOT) is True
