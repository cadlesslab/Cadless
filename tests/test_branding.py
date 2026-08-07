"""Branding pins for the Cadless rename.

The product renamed from its old working name to Cadless; these tests keep
the old brand from creeping back. Old-name literals are assembled from
fragments so this file passes its own scan.
"""

from __future__ import annotations

import json
import os
import re
import tomllib
from pathlib import Path

from tools.leak_guard import BINARY_SUFFIXES, SKIP_DIRS, SKIP_FILE_NAMES

_ROOT = Path(__file__).resolve().parent.parent
_CATALOG = _ROOT / "catalog"

# Assembled old-brand tokens (never written literally here).
_OLD = "vul" + "can"
_OLD_PKG = _OLD + "_text"
_OLD_ENV = _OLD.upper() + "_"
_OLD_PATH = "/apps/" + _OLD
_OLD_DATASET = _OLD + "-samples"
_OLD_DB = _OLD + ".db"

# Files allowed to keep old-brand tokens, with a line budget: the README
# migration note and the one-time DB migration shim need the old names to
# describe what they migrate from.
#
# The leak guard has no counterpart to this budget and should not grow one — a
# credential shape is never worth an allowance. That asymmetry is the one thing
# the two scanners deliberately keep apart; the scan scope they walk is imported
# above from the guard precisely so it cannot drift.
_ALLOWED_LINE_BUDGET = {
    Path("README.md"): 1,
    Path("cadless") / "config.py": 2,
    Path("tests") / "test_branding.py": 0,  # fragments only — a literal is a bug
}


def _hits(root: Path = _ROOT) -> dict[Path, list[int]]:
    pattern = re.compile(_OLD, re.IGNORECASE)
    found: dict[Path, list[int]] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if name in SKIP_FILE_NAMES:
                continue
            path = Path(dirpath) / name
            if path.suffix.lower() in BINARY_SUFFIXES:
                continue
            try:
                text = path.read_text(encoding="utf8", errors="ignore")
            except OSError:
                continue
            lines = [i for i, line in enumerate(text.splitlines(), 1) if pattern.search(line)]
            if lines:
                found[path.relative_to(root)] = lines
    return found


def test_no_stray_old_brand_tokens():
    over_budget = {
        str(rel): lines
        for rel, lines in _hits().items()
        if len(lines) > _ALLOWED_LINE_BUDGET.get(rel, 0)
    }
    assert not over_budget, f"old brand tokens remain: {over_budget}"


def test_scan_scope_is_shared_with_the_leak_guard(tmp_path):
    # The skip scope is defined once, in tools/leak_guard.py. This pins that the
    # sweep actually honours it, so widening or narrowing it there can never
    # quietly stop applying here. Note .git as a *file*, which is how a linked
    # worktree stores it, and .obj as ASCII the scanners read rather than skip.
    (tmp_path / ".git").write_text(f"gitdir: /repo/.git/worktrees/{_OLD}\n")
    (tmp_path / ".next").mkdir()
    (tmp_path / ".next" / "chunk.js").write_text(f"{_OLD}\n")
    (tmp_path / "mesh.glb").write_bytes(_OLD.encode() + b"\n")
    (tmp_path / "model.obj").write_text(f"# {_OLD} export: model\n")
    assert _hits(tmp_path) == {Path("model.obj"): [1]}


def test_package_renamed():
    assert (_ROOT / "cadless").is_dir(), "cadless/ package directory missing"
    assert not (_ROOT / _OLD_PKG).exists(), "old package directory still present"
    meta = tomllib.loads((_ROOT / "pyproject.toml").read_text())
    assert meta["project"]["name"] == "cadless"
    assert meta["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == ["cadless"]


def test_env_prefix_is_cadless():
    from cadless.config import Settings

    assert Settings.model_config["env_prefix"] == "CADLESS_"
    compose = (_ROOT / "docker-compose.yml").read_text()
    assert "CADLESS_" in compose
    assert _OLD_ENV not in compose


def test_compose_pins_project_name():
    # The compose project name prefixes named volumes; pinning it keeps
    # volumes stable regardless of the checkout directory's name.
    compose = (_ROOT / "docker-compose.yml").read_text()
    assert re.search(r"^name: cadless$", compose, re.MULTILINE)


def test_proxy_path_renamed():
    for rel in ("infra/proxy/Caddyfile", "infra/nginx.conf", "docker-compose.yml", "start.sh"):
        text = (_ROOT / rel).read_text()
        assert _OLD_PATH not in text, f"{rel} still routes the old path"
    assert "/apps/cadless" in (_ROOT / "infra" / "proxy" / "Caddyfile").read_text()


def test_dataset_label_renamed():
    # Every bundled domain, not just the mechanical one: the label has to have
    # been renamed everywhere it appears, and content now ships in four domains.
    manifests = sorted(_CATALOG.glob("*/*/manifest.json"))
    assert len(manifests) >= 39
    for manifest in manifests:
        assert json.loads(manifest.read_text())["source"] == "cadless-samples", manifest
    credits = (_ROOT / "CREDITS.md").read_text()
    # Only the label is pinned here. This used to count one mention per bundled
    # item, which worked while the file was a five-row table; the bundled set is
    # now grouped by domain. Per-item credit coverage, and the per-item dataset
    # and licence values, are enforced by tests/test_public_assets.py.
    assert "cadless-samples" in credits
    assert _OLD_DATASET not in credits


def test_frontend_branding():
    index = (_ROOT / "frontend" / "index.html").read_text()
    assert "Cadless" in index and _OLD.capitalize() not in index
    icons = (_ROOT / "frontend" / "src" / "components" / "icons.tsx").read_text()
    assert "CadlessIcon" in icons


def test_db_migration_renames_old_file(tmp_path):
    from cadless.config import Settings

    settings = Settings(data_dir=tmp_path)
    old = tmp_path / _OLD_DB
    old.write_bytes(b"legacy")
    resolved = settings.db_path
    assert resolved == tmp_path / "cadless.db"
    assert resolved.read_bytes() == b"legacy", "old database was not carried over"
    assert not old.exists(), "old database file left behind"


def test_db_migration_prefers_existing_new_file(tmp_path):
    from cadless.config import Settings

    settings = Settings(data_dir=tmp_path)
    (tmp_path / "cadless.db").write_bytes(b"current")
    (tmp_path / _OLD_DB).write_bytes(b"legacy")
    resolved = settings.db_path
    assert resolved.read_bytes() == b"current", "migration must never clobber the new database"


def test_db_path_on_fresh_install(tmp_path):
    from cadless.config import Settings

    settings = Settings(data_dir=tmp_path)
    assert settings.db_path == tmp_path / "cadless.db"
    assert not settings.db_path.exists(), "db_path must not create anything by itself"
