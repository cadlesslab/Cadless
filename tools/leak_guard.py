#!/usr/bin/env python3
"""Repository leak guard.

This project graduated from an extract-and-publish pipeline that scrubbed
internal references at release time. Now that this repository takes direct
commits, the same scan runs here in CI on every push and pull request: the
tree must stay free of internal tracker ids, internal hostnames and paths,
and anything shaped like a credential.

Fail-closed: any unexpected error is a failure, never a pass.

    python tools/leak_guard.py [tree]
    python tools/leak_guard.py --text FILE [FILE ...]

Scans the repository root when no tree is given. ``--text`` scans the given
files instead, for the text a change is *published* with rather than the text
it leaves in the tree -- commit messages and the pull request body, which no
tree walk can reach. Exit code 0 means clean, 1 means findings (or an internal
error), 2 means bad usage.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

# A few pattern sources are plain literals that would match themselves; they
# are assembled from fragments so this file passes its own scan. A regex-shaped
# pattern is exempt only while its own source text cannot match it — spelling a
# literal example into one (an alternation, say) would fail this file forever.
_DESIGN_DOCS = "plan" + "-docs"
_INTERNAL_HOST = "fold" + "less"
_DEV_HOME = "/home/" + "ubuntu"
_INTERNAL_DOMAIN = "inno" + "lingua"
# The hosted half's own names, assembled like the four above so this file stays
# clean under its own scan — and so that editing one of the patterns below can
# never turn it into a self-matching literal by accident.
_PLATFORM = "cad" + "less"
_MARKET_SETTING = "MARKET" + "_API_BASE"
# Two directory names from the other repository. Both are bare literals, so they
# are assembled for the same reason the settings key is.
_AUTHORING_DIR = "catalog_" + "authoring"
_PLUGIN_DIR = "engine_" + "plugin"

INTERNAL_PATTERNS: dict[str, str] = {
    "internal ticket id": r"VT3-\d{3,4}",
    "internal tracker key": r"(?i)INNOL-\d+",
    "internal epic id": r"\bEpic \d{4}\b",
    "internal design-doc path": _DESIGN_DOCS,
    "internal infra hostname": "(?i)" + _INTERNAL_HOST,
    "old CI vendor": r"(?i)azure\s*devops",
    "dev-machine absolute path": _DEV_HOME,
    "internal domain": "(?i)" + _INTERNAL_DOMAIN + r"\.(?:ai|com)",
    # This repository carries the engine and its seams; the hosted service that
    # plugs into them lives in a separate, private repository. Its domain, its
    # settings key and its distribution name are the shapes that reappear first
    # when that boundary is crossed back, so all three fail the build closed.
    #
    # What these promise, and what they do not: they are a tripwire on the names
    # the hosted half actually carries, not proof that no platform code is in
    # the tree. Something written from scratch under different names would pass.
    # Measured against the real plugin package, the three together flag 26 of
    # its 38 files where the first two flagged 12.
    #
    # The other repository is not named anywhere in this tree — not its name, not
    # its directories, not its modules — and these patterns are what keeps it
    # that way. A reader needs the shape (engine and seams here, implementations
    # elsewhere, the dependency one-way), and none of that needs a name.
    #
    # This project's own identity stays sayable: what is fenced is the hosted
    # domain rather than the shared prefix, and the private compound names rather
    # than the ordinary words "market" or "platform". The clone URL, the package
    # name and the CADLESS_ config prefix all pass, and a test pins that down.
    #
    # The domain ends on a word boundary so this project's own dotted namespace
    # is not swept up: a future module whose name merely begins with those two
    # letters is not a hosted endpoint. A bare module path that spells the whole
    # domain does still match — a chosen collision, not an oversight.
    "platform endpoint": "(?i)" + _PLATFORM + r"\.ai\b",
    "platform settings key": "(?i)" + _MARKET_SETTING,
    "platform namespace": "(?i)" + _PLATFORM + r"[_-](?:market|platform)",
    "platform authoring directory": _AUTHORING_DIR,
    "platform plugin directory": _PLUGIN_DIR,
}

SECRET_PATTERNS: dict[str, str] = {
    "Anthropic API key": r"sk-ant-[A-Za-z0-9_-]{20}",
    "OpenAI API key": r"\bsk-[A-Za-z0-9]{32,}",
    "AWS access key": r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b",
    "GitHub token": r"\b(?:ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})",
    "Google API key": r"\bAIza[0-9A-Za-z_-]{30,}",
    "Slack token": r"\bxox[baprs]-[0-9A-Za-z-]{10,}",
    "private key block": r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
}

# The three sets below are this repository's single definition of scan scope —
# what counts as source worth reading. tests/test_branding.py imports them so
# the branding sweep and this gate cannot drift apart: widen or narrow the
# scope here and both scanners follow.
#
# What is deliberately not shared is the exception model. This gate has no
# allowlist of any kind (fail-closed, see the module docstring): a leaked
# credential is not something to grant a budget to. The branding sweep does
# carry a per-file line budget, because it guards a rename that is still
# finishing and a couple of files must name the old brand to describe it.
SKIP_DIRS = {
    ".git",
    ".claude",
    "node_modules",
    "__pycache__",
    ".venv",
    ".pytest_cache",
    "dist",
    "build",
    ".next",
}

# A linked worktree carries .git as a file holding an absolute gitdir path
# rather than as a directory, so pruning directory names alone leaves git's
# own metadata in scope. Names here are skipped as files, not as directories.
SKIP_FILE_NAMES = {".git"}

# Formats whose bytes are not prose. STEP and OBJ are pointedly absent: both
# are ASCII, and their headers are where an exporter records a source path, an
# author or an organisation — the very shapes this guard looks for. Reading
# them costs a few hundred kilobytes and closes a blind spot.
BINARY_SUFFIXES = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".avif",
        ".ico",
        ".glb",
        ".stl",
        ".mp3",
        ".woff",
        ".woff2",
        ".ttf",
        ".ktx2",
        ".pdf",
    }
)


def iter_text_files(root: Path):
    """Yield (path, text) for every scannable text file under root."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if name in SKIP_FILE_NAMES:
                continue
            path = Path(dirpath) / name
            if path.suffix.lower() in BINARY_SUFFIXES:
                continue
            try:
                yield path, path.read_text(encoding="utf8")
            except (UnicodeDecodeError, OSError):
                continue


#: The address inside a Developer Certificate of Origin trailer.
#:
#: `CONTRIBUTING.md` requires the trailer on every commit and `git commit -s`
#: writes it from the author's own address, so matching the domain patterns
#: against it would fail every commit for carrying the very thing that makes it
#: conform. It is not a leak either: GitHub already publishes that address in
#: the commit's author field, where no scan of the message could reach it.
#:
#: Only the address is exempt, not the line. A tracker key written into the name
#: is still found, and so is one anywhere else in the message -- which is where
#: the two commits this was added for actually put theirs.
_SIGN_OFF_ADDRESS = re.compile(r"(?im)^(Signed-off-by:[^<>\n]*)<[^<>\s]+>(\s*)$")


def _compiled():
    """Every pattern, compiled once. One set, whichever way the text arrives."""
    return [
        (label, name, re.compile(rx))
        for label, patterns in (("internal", INTERNAL_PATTERNS), ("secret", SECRET_PATTERNS))
        for name, rx in patterns.items()
    ]


def _findings_in(text: str, where: str, compiled) -> list[str]:
    """One finding line per match in a single piece of text."""
    found: list[str] = []
    for label, name, rx in compiled:
        for match in rx.finditer(text):
            line = text[: match.start()].count("\n") + 1
            snippet = text[max(0, match.start() - 30) : match.end() + 20].replace("\n", " ").strip()
            found.append(f"[{label}/{name}] {where}:{line}  …{snippet}…")
    return found


def scan_tree(root: Path) -> list[str]:
    """Return one finding line per pattern match found under root."""
    compiled = _compiled()
    findings: list[str] = []
    for path, text in iter_text_files(root):
        findings += _findings_in(text, str(path.relative_to(root)), compiled)
    return findings


def scan_text(paths: list[Path]) -> list[str]:
    """Return one finding line per pattern match in the given text files.

    Exists because a commit message is not in the tree, and neither is a pull
    request body. :func:`scan_tree` reaches its patterns through ``os.walk``, so
    the pattern most likely to appear in a subject line -- the internal tracker
    key -- was the one nothing could see. Two commits reached this repository's
    ``origin`` carrying one while every check passed.

    Catching it *before* the push is the only catch that helps: a pull request
    mints ``refs/pull/N/head`` the moment it opens, and GitHub never deletes
    those, so a key that gets that far cannot be taken back by rewriting the
    branch.

    The sign-off address is exempt -- see :data:`_SIGN_OFF_ADDRESS`. Nothing else
    is: this reads the same pattern set the tree scan does.

    Raises rather than skipping a file it cannot read -- see :func:`run_text`.
    """
    compiled = _compiled()
    findings: list[str] = []
    for path in paths:
        text = path.read_text(encoding="utf-8", errors="replace")
        # Blanked rather than removed, so the line numbers reported still count
        # from the top of the file the reader will open.
        text = _SIGN_OFF_ADDRESS.sub(r"\1\2", text)
        findings += _findings_in(text, path.name, compiled)
    return findings


def run(root: Path) -> bool:
    """Return True when the tree is clean. Any error counts as a failure."""
    try:
        findings = scan_tree(root)
    except Exception as exc:  # fail-closed: an unscanned tree is not a clean tree
        print(f"leak guard errored — treating as failure: {exc!r}", file=sys.stderr)
        return False

    if not findings:
        print("leak guard passed — no internal references or credential shapes found")
        return True

    print(f"leak guard failed — {len(findings)} finding(s)", file=sys.stderr)
    for finding in findings[:60]:
        print(f"  {finding}", file=sys.stderr)
    if len(findings) > 60:
        print(f"  … and {len(findings) - 60} more", file=sys.stderr)
    return False


def run_text(paths: list[Path]) -> bool:
    """Return True when every given file is clean. Any error counts as a failure.

    **No input is a failure.** Being handed nothing to check is a wiring mistake
    in whatever assembled the arguments, and answering "clean" would hide that
    mistake behind a pass -- which is the same shape of hole this function was
    added to close.
    """
    if not paths:
        print("leak guard errored — nothing to scan", file=sys.stderr)
        return False
    try:
        findings = scan_text(paths)
    except Exception as exc:  # fail-closed: text that was not read is not clean text
        print(f"leak guard errored — treating as failure: {exc!r}", file=sys.stderr)
        return False

    if not findings:
        print(f"leak guard passed — {len(paths)} text input(s) carry no internal references")
        return True

    print(f"leak guard failed — {len(findings)} finding(s) in published text", file=sys.stderr)
    for finding in findings[:60]:
        print(f"  {finding}", file=sys.stderr)
    if len(findings) > 60:
        print(f"  … and {len(findings) - 60} more", file=sys.stderr)
    print(
        "This text is published and cannot be taken back once a pull request exists. "
        "Reword the commit or the body, then force-push before merging.",
        file=sys.stderr,
    )
    return False


def main(argv: list[str]) -> int:
    if len(argv) > 1 and argv[1] == "--text":
        return 0 if run_text([Path(arg) for arg in argv[2:]]) else 1
    if len(argv) > 2:
        print(__doc__)
        return 2
    root = Path(argv[1]).resolve() if len(argv) == 2 else Path(__file__).resolve().parent.parent
    return 0 if run(root) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
