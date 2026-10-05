#!/usr/bin/env python3
"""Pull-request title check.

Pull requests here are squash-merged with the title as the commit subject, and
the release pull request computes the next version from those subjects. A title
therefore has to say what kind of change it is:

    type(scope)!: description

``type`` is one of :data:`TYPES`; the ``(scope)`` is optional; a ``!`` marks a
breaking change; the description is not empty.

Fail-closed: a title that could not be read is a failure, never a pass.

    python tools/check_pr_title.py FILE

FILE holds the title. Exit code 0 means it conforms, 1 means it does not (or
could not be read), 2 means bad usage.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# The types come from the release configuration's changelog sections rather than
# from a second list here. A type those sections do not know is dropped from the
# changelog, so a title carrying it could never cause a release, and two lists
# kept by hand would drift apart without anything failing.
_RELEASE_CONFIG = Path(__file__).resolve().parent.parent / "release-please-config.json"

TYPES = tuple(
    section["type"]
    for section in json.loads(_RELEASE_CONFIG.read_text(encoding="utf-8"))["changelog-sections"]
)

_TITLE = re.compile(
    r"(?:" + "|".join(map(re.escape, TYPES)) + r")(?:\([A-Za-z0-9_./-]+\))?!?: \S.*"
)


def conforms(title: str) -> bool:
    """Return True when the title is ``type(scope)!: description`` with a known type."""
    return _TITLE.fullmatch(title.strip()) is not None


def run(path: Path) -> bool:
    """Return True when the title in path conforms. Any error counts as a failure."""
    try:
        title = path.read_text(encoding="utf-8").strip()
    except Exception as exc:  # fail-closed: a title that was not read is not a valid one
        print(f"title check errored -- treating as failure: {exc!r}", file=sys.stderr)
        return False

    if conforms(title):
        print(f"title check passed -- {title!r}")
        return True

    print(f"title check failed -- {title!r}", file=sys.stderr)
    print(
        "Expected `type(scope)!: description`, where type is one of "
        + ", ".join(TYPES)
        + ", the scope is optional and ! marks a breaking change. See CONTRIBUTING.md.",
        file=sys.stderr,
    )
    return False


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    return 0 if run(Path(argv[1])) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
