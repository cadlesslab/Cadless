"""Tests for the release artifact helper (tools/release_artifacts.py)."""

from __future__ import annotations

import io
import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest

from tests.git_env import isolated_git_env
from tools import release_artifacts as ra


def _tree(root: Path, backend: str = "1.2.3", frontend: str = "4.5.6") -> Path:
    """A minimal tree carrying both versions, committed so `git archive` can read it."""
    (root / "cadless").mkdir(parents=True)
    (root / "cadless" / "__init__.py").write_text(f'"""Pkg."""\n\n__version__ = "{backend}"\n')
    (root / "frontend" / "src").mkdir(parents=True)
    (root / "frontend" / "version.txt").write_text(f"{frontend}\n")
    (root / "frontend" / "src" / "App.tsx").write_text("export const App = () => null;\n")
    (root / "catalog" / "parts").mkdir(parents=True)
    (root / "catalog" / "parts" / "bracket.json").write_text('{"name": "bracket"}\n')
    (root / "constraints.txt").write_text("pillow==12.3.0\n")
    git = ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.com"]
    env = isolated_git_env()
    subprocess.run([*git, "init", "-q"], check=True, env=env)
    subprocess.run([*git, "add", "-A"], check=True, env=env)
    subprocess.run([*git, "commit", "-q", "-m", "tree"], check=True, env=env)
    return root


def _targz(path: Path, members: dict[str, bytes]) -> Path:
    with tarfile.open(path, "w:gz") as archive:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return path


def _built_backend(outdir: Path, version: str = "1.2.3", leak_guard: bool = True) -> None:
    """What hatchling leaves in outdir: a wheel and an sdist."""
    outdir.mkdir()
    with zipfile.ZipFile(outdir / f"cadless-{version}-py3-none-any.whl", "w") as wheel:
        wheel.writestr("cadless/__init__.py", f'__version__ = "{version}"\n')
    members = {f"cadless-{version}/pyproject.toml": b"[project]\n"}
    if leak_guard:
        members[f"cadless-{version}/tools/leak_guard.py"] = b"# guard\n"
    _targz(outdir / f"cadless-{version}.tar.gz", members)


PLANTED = f"see {ra._PLANTED}\n".encode()

# A digit outside ASCII, which `\d` would accept without the ASCII flag.
ARABIC_DIGIT_TAG = "backend-v" + chr(0x661) + ".2.3"


# Tags --------------------------------------------------------------------------


def test_a_tag_naming_the_trees_version_is_accepted(tmp_path):
    root = _tree(tmp_path)
    assert ra.check_tag("backend-v1.2.3", root) == "backend"
    assert ra.check_tag("frontend-v4.5.6", root) == "frontend"


def test_a_tag_naming_another_version_is_refused(tmp_path):
    """The case an edited release pull request title produces."""
    root = _tree(tmp_path)
    with pytest.raises(ValueError, match="carries 1.2.3"):
        ra.check_tag("backend-v9.9.9", root)


@pytest.mark.parametrize(
    "tag",
    [
        "v1.2.3",
        "backend-1.2.3",
        "backend-v1.2",
        "backend-v1.2.3.4",
        "docs-v1.2.3",
        ARABIC_DIGIT_TAG,
    ],
)
def test_a_tag_that_is_not_a_component_release_is_refused(tmp_path, tag):
    with pytest.raises(ValueError, match="not a release tag"):
        ra.check_tag(tag, _tree(tmp_path))


def test_the_cli_prints_the_component_and_nothing_else(capsys):
    """The workflows read this line as the component's name."""
    tag = f"backend-v{ra.version('backend')}"
    assert ra.main(["release_artifacts.py", "check-tag", tag]) == 0
    assert capsys.readouterr().out == "backend\n"


# Packing ------------------------------------------------------------------------


def test_the_backend_release_adds_the_constraints_and_the_catalog(tmp_path):
    root = _tree(tmp_path / "repo")
    outdir = tmp_path / "dist"
    _built_backend(outdir)
    names = [p.name for p in ra.pack("backend", outdir, root)]
    assert names == [
        "cadless-1.2.3-constraints.txt",
        "cadless-1.2.3-py3-none-any.whl",
        "cadless-1.2.3.tar.gz",
        "cadless-catalog-1.2.3.tar.gz",
    ]
    with tarfile.open(outdir / "cadless-catalog-1.2.3.tar.gz") as archive:
        assert archive.getnames() == ["catalog", "catalog/parts", "catalog/parts/bracket.json"]


def test_an_sdist_without_the_leak_guard_is_refused(tmp_path):
    root = _tree(tmp_path / "repo")
    outdir = tmp_path / "dist"
    _built_backend(outdir, leak_guard=False)
    with pytest.raises(ValueError, match="tools/leak_guard.py"):
        ra.pack("backend", outdir, root)


def test_the_backend_release_needs_the_built_wheel(tmp_path):
    root = _tree(tmp_path / "repo")
    with pytest.raises(FileNotFoundError, match="whl"):
        ra.pack("backend", tmp_path / "empty", root)


def test_the_frontend_release_is_its_source(tmp_path):
    root = _tree(tmp_path / "repo")
    outdir = tmp_path / "dist"
    names = [p.name for p in ra.pack("frontend", outdir, root)]
    assert names == ["cadless-frontend-4.5.6.tar.gz"]
    with tarfile.open(outdir / names[0]) as archive:
        assert "frontend/src/App.tsx" in archive.getnames()
        assert not any(name.startswith("catalog") for name in archive.getnames())


# Scanning -----------------------------------------------------------------------


def test_the_scan_finds_a_reference_inside_a_tarball(tmp_path):
    _targz(tmp_path / "cadless-catalog-1.2.3.tar.gz", {"catalog/a.md": PLANTED})
    findings = ra.scan(tmp_path)
    assert findings and findings[0].startswith("cadless-catalog-1.2.3.tar.gz: ")


def test_the_scan_finds_a_reference_inside_a_wheel(tmp_path):
    with zipfile.ZipFile(tmp_path / "cadless-1.2.3-py3-none-any.whl", "w") as wheel:
        wheel.writestr("cadless/notes.py", PLANTED)
    assert ra.scan(tmp_path)


def test_the_scan_finds_a_reference_in_a_plain_file(tmp_path):
    (tmp_path / "cadless-1.2.3-constraints.txt").write_bytes(PLANTED)
    assert ra.scan(tmp_path)


@pytest.mark.parametrize(
    "member",
    [
        "catalog/part.glb",  # a suffix the guard's walk skips as binary
        "catalog/build/part.step",  # a directory the guard's walk prunes
    ],
)
def test_the_scan_reads_what_the_guards_walk_would_skip(tmp_path, member):
    """Binary headers are where an exporter writes a path or a name."""
    _targz(tmp_path / "cadless-catalog-1.2.3.tar.gz", {member: b"glTF\x02\x00\xff" + PLANTED})
    assert ra.scan(tmp_path)


def test_the_scan_finds_a_reference_in_a_path(tmp_path):
    _targz(tmp_path / "cadless-catalog-1.2.3.tar.gz", {f"catalog/{ra._PLANTED}/part.step": b"x\n"})
    assert ra.scan(tmp_path)


def test_the_scan_finds_a_reference_written_in_utf16(tmp_path):
    _targz(
        tmp_path / "cadless-catalog-1.2.3.tar.gz",
        {"catalog/a.step": PLANTED.decode().encode("utf-16-le")},
    )
    assert ra.scan(tmp_path)


def test_the_scan_passes_clean_artifacts(tmp_path):
    _targz(tmp_path / "cadless-frontend-4.5.6.tar.gz", {"frontend/a.ts": b"export {};\n"})
    (tmp_path / "cadless-1.2.3-constraints.txt").write_text("pillow==12.3.0\n")
    assert ra.scan(tmp_path) == []


def test_the_scan_refuses_a_link_it_would_not_read(tmp_path):
    with tarfile.open(tmp_path / "cadless-catalog-1.2.3.tar.gz", "w:gz") as archive:
        link = tarfile.TarInfo("catalog/notes")
        link.type = tarfile.SYMTYPE
        link.linkname = ra._PLANTED
        archive.addfile(link)
    with pytest.raises(ValueError, match="links"):
        ra.scan(tmp_path)


@pytest.mark.parametrize("second", ["catalog/notes.md", "./catalog/notes.md"])
def test_the_scan_refuses_a_member_carried_twice(tmp_path, second):
    """Extracting keeps one copy, so the other would ship unread."""
    with tarfile.open(tmp_path / "cadless-catalog-1.2.3.tar.gz", "w:gz") as archive:
        for name, data in [("catalog/notes.md", PLANTED), (second, b"clean\n")]:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    with pytest.raises(ValueError, match="more than once"):
        ra.scan(tmp_path)


def test_the_scan_refuses_a_wheel_member_carried_twice(tmp_path):
    with zipfile.ZipFile(tmp_path / "cadless-1.2.3-py3-none-any.whl", "w") as archive:
        archive.writestr("cadless/notes.txt", PLANTED)
        with pytest.warns(UserWarning, match="Duplicate name"):
            archive.writestr("cadless/notes.txt", b"clean\n")
    with pytest.raises(ValueError, match="more than once"):
        ra.scan(tmp_path)


@pytest.mark.parametrize(
    ("artifact", "name"),
    [
        ("cadless-catalog-1.2.3.tar.gz", "../escaped.md"),
        ("cadless-catalog-1.2.3.tar.gz", "/catalog/notes.md"),
        ("cadless-1.2.3-py3-none-any.whl", "../cadless/notes.txt"),
        ("cadless-1.2.3-py3-none-any.whl", "/cadless/notes.txt"),
    ],
)
def test_the_scan_refuses_a_member_named_outside_its_root(tmp_path, artifact, name):
    """Extraction rewrites such a name onto a path inside, where another member may be."""
    if artifact.endswith(".whl"):
        with zipfile.ZipFile(tmp_path / artifact, "w") as archive:
            archive.writestr(name, PLANTED)
    else:
        _targz(tmp_path / artifact, {name: PLANTED})
    with pytest.raises(ValueError, match="outside its root"):
        ra.scan(tmp_path)


def test_the_scan_refuses_a_link_a_later_member_would_be_written_through(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    with tarfile.open(tmp_path / "cadless-catalog-1.2.3.tar.gz", "w:gz") as archive:
        link = tarfile.TarInfo("catalog/out")
        link.type = tarfile.SYMTYPE
        link.linkname = str(outside)
        archive.addfile(link)
        member = tarfile.TarInfo("catalog/out/escaped.md")
        member.size = len(PLANTED)
        archive.addfile(member, io.BytesIO(PLANTED))
    with pytest.raises(tarfile.FilterError):
        ra.scan(tmp_path)
    assert not (outside / "escaped.md").exists()


def test_the_scan_finds_a_reference_written_in_big_endian_utf16(tmp_path):
    _targz(
        tmp_path / "cadless-catalog-1.2.3.tar.gz",
        {"catalog/a.step": PLANTED.decode().encode("utf-16-be")},
    )
    assert ra.scan(tmp_path)


def test_the_scan_refuses_an_archive_whatever_its_name(tmp_path):
    _targz(
        tmp_path / "cadless-catalog-1.2.3.tar.gz", {"catalog/data.npz": b"PK\x03\x04" + bytes(26)}
    )
    with pytest.raises(ValueError, match="would not open"):
        ra.scan(tmp_path)


def test_the_scan_refuses_an_archive_it_would_not_open(tmp_path):
    _targz(
        tmp_path / "cadless-catalog-1.2.3.tar.gz", {"catalog/more.zip": b"PK\x05\x06" + bytes(18)}
    )
    with pytest.raises(ValueError, match="would not open"):
        ra.scan(tmp_path)


def test_the_scan_refuses_patterns_that_find_nothing(tmp_path, monkeypatch):
    """Patterns that miss a planted sample would pass any artifact."""
    (tmp_path / "cadless-1.2.3-constraints.txt").write_text("pillow==12.3.0\n")
    monkeypatch.setattr(ra, "_compiled", lambda: [])
    with pytest.raises(RuntimeError, match="planted"):
        ra.scan(tmp_path)


def test_the_scan_refuses_an_empty_directory(tmp_path):
    with pytest.raises(FileNotFoundError, match="no artifacts"):
        ra.scan(tmp_path)


def test_the_cli_fails_on_a_finding(tmp_path):
    (tmp_path / "cadless-1.2.3-constraints.txt").write_bytes(PLANTED)
    assert ra.main(["release_artifacts.py", "scan", str(tmp_path)]) == 1


def test_the_cli_fails_closed_on_a_refusal(capsys):
    assert ra.main(["release_artifacts.py", "check-tag", "v1.2.3"]) == 1
    assert "not a release tag" in capsys.readouterr().err


def test_the_cli_refuses_to_run_without_a_command():
    """A miswired call names nothing; answering as if it had would hide the miswiring."""
    assert ra.main(["release_artifacts.py"]) == 2


# Verifying what is about to be attached -----------------------------------------------


def _files(outdir: Path, names: set[str]) -> Path:
    outdir.mkdir()
    for name in names:
        (outdir / name).write_text("x\n")
    return outdir


@pytest.mark.parametrize(
    ("component", "tag"), [("backend", "backend-v1.2.3"), ("frontend", "frontend-v4.5.6")]
)
def test_what_is_packed_is_what_a_release_carries(tmp_path, component, tag):
    """Attaching refuses anything else, so the two lists must not drift apart."""
    root = _tree(tmp_path / "repo")
    outdir = tmp_path / "dist"
    if component == "backend":
        _built_backend(outdir)
    packed = {p.name for p in ra.pack(component, outdir, root)}
    if component == "backend":
        packed.add("cadless-1.2.3-images.txt")  # written when the images are pushed
    assert packed == ra.carried(tag)


def test_the_files_a_release_carries_are_accepted(tmp_path):
    ra.verify("backend-v1.2.3", _files(tmp_path / "dist", ra.carried("backend-v1.2.3")))
    ra.verify("frontend-v4.5.6", _files(tmp_path / "web", {"cadless-frontend-4.5.6.tar.gz"}))


@pytest.mark.parametrize(
    ("tag", "names"),
    [
        ("backend-v1.2.3", ra.carried("backend-v1.2.3") - {"cadless-1.2.3-images.txt"}),
        ("backend-v1.2.3", ra.carried("backend-v1.2.4")),
        ("frontend-v4.5.6", {"cadless-frontend-4.5.6.tar.gz", "cadless-4.5.6.tar.gz"}),
    ],
    ids=["a file missing", "another version's files", "the other component's file too"],
)
def test_files_that_are_not_exactly_the_releases_are_refused(tmp_path, tag, names):
    with pytest.raises(ValueError, match="carries"):
        ra.verify(tag, _files(tmp_path / "dist", names))
