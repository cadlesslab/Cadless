#!/usr/bin/env python3
"""Release artifacts: what a component's release carries, and the checks before it ships.

Each component releases its own files, built from the commit its tag names:

- the backend: the wheel and the sdist (built by hatchling beforehand), the
  dependency constraints and the bundled catalog;
- the frontend: its source, which whoever places their own panels in it builds.

Nothing is published from here. The release workflows attach what this builds
only after its checks: the tag names the version its tree carries, the leak
guard's patterns find nothing in any artifact, and the files to attach are
exactly the ones the tag's release carries.

    python tools/release_artifacts.py check-tag TAG
    python tools/release_artifacts.py pack COMPONENT OUTDIR
    python tools/release_artifacts.py scan OUTDIR
    python tools/release_artifacts.py verify TAG OUTDIR

COMPONENT is ``backend`` or ``frontend``. ``check-tag`` prints the tag's
component and nothing else. Fail-closed: any error is a failure, never a pass.
Exit code 0 means done, 1 a refusal or an error, 2 bad usage.
"""

from __future__ import annotations

import posixpath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# The guard's own patterns and matcher rather than its tree walk: the walk skips
# binary suffixes, files that are not UTF-8 and some directory names, and a
# release ships all of those. Its glTF headers, PNG text chunks and STL headers
# are where an exporter writes a path or a name.
from tools.leak_guard import _compiled, _findings_in  # noqa: E402

COMPONENTS = ("backend", "frontend")

_VERSION = re.compile(r"\d+\.\d+\.\d+", re.ASCII)
_TAG = re.compile(r"(backend|frontend)-v(\d+\.\d+\.\d+)", re.ASCII)
_BACKEND_VERSION = re.compile(r'^__version__ = "([^"]+)"$', re.MULTILINE)

# An archive inside an artifact would ship content the scan never opened.
_NESTED_ARCHIVES = (".zip", ".whl", ".tar", ".tgz", ".gz", ".bz2", ".xz", ".7z", ".3mf")
# The same, known by its first bytes whatever it is named: zip, gzip, bzip2, xz
# and 7z.
_ARCHIVE_MAGIC = (b"PK\x03\x04", b"\x1f\x8b", b"BZh", b"\xfd7zXZ\x00", b"7z\xbc\xaf\x27\x1c")


def _is_archive(path: Path) -> bool:
    with path.open("rb") as handle:
        head = handle.read(8)
    return path.name.lower().endswith(_NESTED_ARCHIVES) or head.startswith(_ARCHIVE_MAGIC)


# How each file's bytes are read for the patterns: every byte as a character,
# which keeps the ASCII text inside a binary format, and both UTF-16 orders.
_ENCODINGS = ("latin-1", "utf-16-le", "utf-16-be")

# A reference the guard must find, assembled so this file passes the guard's own
# scan. If a scan of it comes back clean, a clean scan of the artifacts says
# nothing — the patterns are matching nothing — so the scan refuses instead.
_PLANTED = "INN" + "OL-1012"


def version(component: str, root: Path = ROOT) -> str:
    """The version the component's own file carries in the tree at root."""
    if component == "backend":
        found = _BACKEND_VERSION.search((root / "cadless" / "__init__.py").read_text("utf-8"))
        text = found.group(1) if found else ""
    elif component == "frontend":
        text = (root / "frontend" / "version.txt").read_text("utf-8").strip()
    else:
        raise ValueError(f"unknown component {component!r}; expected one of {COMPONENTS}")
    if not _VERSION.fullmatch(text):
        raise ValueError(f"the {component} carries no X.Y.Z version (found {text!r})")
    return text


def _parse(tag: str) -> tuple[str, str]:
    """The component and version a release tag names."""
    match = _TAG.fullmatch(tag)
    if not match:
        raise ValueError(f"{tag!r} is not a release tag (backend-vX.Y.Z or frontend-vX.Y.Z)")
    return match.group(1), match.group(2)


def check_tag(tag: str, root: Path = ROOT) -> str:
    """Return the tag's component when the tag names the version the tree carries.

    A release's version comes from its pull request's title, and anyone who can
    push may edit a title. The tree is what the artifacts are built from, so a
    tag that disagrees with it gets no artifacts: the tag and its release exist
    by then, but nothing is attached to them or pushed under their version.
    """
    component, tagged = _parse(tag)
    in_tree = version(component, root)
    if in_tree != tagged:
        raise ValueError(f"{tag} names {tagged}, but the tree's {component} carries {in_tree}")
    return component


def carried(tag: str) -> set[str]:
    """The names of the files the tag's release carries."""
    component, v = _parse(tag)
    if component == "frontend":
        return {f"cadless-frontend-{v}.tar.gz"}
    return {
        f"cadless-{v}-py3-none-any.whl",
        f"cadless-{v}.tar.gz",
        f"cadless-{v}-constraints.txt",
        f"cadless-catalog-{v}.tar.gz",
        f"cadless-{v}-images.txt",
    }


def verify(tag: str, outdir: Path) -> None:
    """Refuse unless outdir holds exactly the files the tag's release carries.

    The files reach the attaching job from another job of the same run, so this
    is what ties them to the tag's component and version: files built for
    another version, or for the other component, carry other names. What ties
    them to the tag's commit is the build checking out the tag itself.
    """
    expected = carried(tag)
    present = {p.name for p in outdir.iterdir()} if outdir.is_dir() else set()
    if present != expected:
        missing, extra = sorted(expected - present), sorted(present - expected)
        raise ValueError(f"{tag} carries {sorted(expected)}; missing {missing}, unexpected {extra}")


def _git_archive(root: Path, path: str, out: Path) -> None:
    # A commit rather than a bare tree, so every entry keeps the commit's time and
    # the same tag, archived by the same git, produces the same bytes.
    command = ["git", "-C", str(root), "archive", "--format=tar.gz", "-o", str(out), "HEAD", path]
    subprocess.run(command, check=True)


def pack(component: str, outdir: Path, root: Path = ROOT) -> list[Path]:
    """Write the component's remaining artifacts into outdir and return every artifact there.

    For the backend, outdir must already hold the wheel and the sdist; the sdist
    has to carry the leak guard, so that a release can be reviewed with the
    guard of the version it carries.
    """
    v = version(component, root)
    outdir.mkdir(parents=True, exist_ok=True)
    if component == "backend":
        wheel = outdir / f"cadless-{v}-py3-none-any.whl"
        sdist = outdir / f"cadless-{v}.tar.gz"
        for built in (wheel, sdist):
            if not built.is_file():
                raise FileNotFoundError(f"{built.name} is missing; build it with hatchling first")
        with tarfile.open(sdist) as archive:
            if f"cadless-{v}/tools/leak_guard.py" not in archive.getnames():
                raise ValueError(f"{sdist.name} does not carry tools/leak_guard.py")
        shutil.copyfile(root / "constraints.txt", outdir / f"cadless-{v}-constraints.txt")
        _git_archive(root, "catalog", outdir / f"cadless-catalog-{v}.tar.gz")
    else:
        _git_archive(root, "frontend", outdir / f"cadless-frontend-{v}.tar.gz")
    return sorted(p for p in outdir.iterdir() if p.is_file())


def _one_path_each(artifact: Path, names: list[str]) -> None:
    """Refuse members that extraction would not give a path of their own.

    Two members can land on one path, and then only one of them is read: a name
    carried twice, or one that extraction rewrites — zipfile drops a leading
    slash and `..`, the tar data filter a leading slash — onto another's path.
    """
    outside = sorted(n for n in names if posixpath.isabs(n) or ".." in n.split("/"))
    if outside:
        raise ValueError(f"{artifact.name} carries members named outside its root: {outside}")
    counts = Counter(posixpath.normpath(name) for name in names)
    repeated = sorted(name for name, count in counts.items() if count > 1)
    if repeated:
        raise ValueError(f"{artifact.name} carries members more than once: {repeated}")


def _unpack(artifact: Path, into: Path) -> None:
    if artifact.suffix == ".whl":
        with zipfile.ZipFile(artifact) as archive:
            _one_path_each(artifact, archive.namelist())
            archive.extractall(into)
    elif artifact.name.endswith(".tar.gz"):
        with tarfile.open(artifact) as archive:
            _one_path_each(artifact, archive.getnames())
            archive.extractall(into, filter="data")
    else:
        shutil.copyfile(artifact, into / artifact.name)


def _findings_under(root: Path) -> list[str]:
    """Every pattern match in every file under root, its path included, whatever its format."""
    compiled = _compiled()
    findings: list[str] = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        data = path.read_bytes()
        where = str(path.relative_to(root))
        findings += _findings_in(where, f"{where} (its path)", compiled)
        for encoding in _ENCODINGS:
            for finding in _findings_in(data.decode(encoding, errors="replace"), where, compiled):
                if finding not in findings:
                    findings.append(finding)
    return findings


def scan(outdir: Path) -> list[str]:
    """What the leak guard's patterns find in the contents of every artifact in outdir."""
    artifacts = sorted(p for p in outdir.iterdir() if p.is_file())
    if not artifacts:
        raise FileNotFoundError(f"{outdir} holds no artifacts to scan")
    findings: list[str] = []
    with tempfile.TemporaryDirectory() as scratch:
        control = Path(scratch) / "control"
        control.mkdir()
        (control / "sample.bin").write_bytes(b"\x00\xff" + _PLANTED.encode() + b"\x00")
        if not _findings_under(control):
            raise RuntimeError("the leak guard's patterns found nothing in a planted sample")
        for artifact in artifacts:
            unpacked = Path(scratch) / f"unpacked-{artifact.name}"
            unpacked.mkdir()
            _unpack(artifact, unpacked)
            # A link is read as the file it points at, or not at all when that
            # is missing, so the text of the link itself would ship unread.
            links = sorted(
                str(p.relative_to(unpacked)) for p in unpacked.rglob("*") if p.is_symlink()
            )
            if links:
                raise ValueError(f"{artifact.name} holds links the scan would not read: {links}")
            nested = sorted(
                str(p.relative_to(unpacked))
                for p in unpacked.rglob("*")
                if p.is_file() and _is_archive(p)
            )
            if nested:
                raise ValueError(
                    f"{artifact.name} holds archives the scan would not open: {nested}"
                )
            findings += [f"{artifact.name}: {finding}" for finding in _findings_under(unpacked)]
    return findings


def main(argv: list[str]) -> int:
    command, args = (argv[1], argv[2:]) if len(argv) > 1 else ("", [])
    try:
        if command == "check-tag" and len(args) == 1:
            print(check_tag(args[0]))
        elif command == "pack" and len(args) == 2 and args[0] in COMPONENTS:
            for artifact in pack(args[0], Path(args[1])):
                print(artifact.name)
        elif command == "verify" and len(args) == 2:
            verify(args[0], Path(args[1]))
            print(f"{args[0]}: the files are the ones the release carries")
        elif command == "scan" and len(args) == 1:
            findings = scan(Path(args[0]))
            for finding in findings:
                print(finding, file=sys.stderr)
            if findings:
                print(
                    f"release artifacts: {len(findings)} internal reference(s) found",
                    file=sys.stderr,
                )
                return 1
            print("release artifacts: the leak guard's patterns found nothing")
        else:
            print(__doc__)
            return 2
    except Exception as exc:  # fail-closed: an artifact that could not be checked is not shipped
        print(f"release artifacts: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
