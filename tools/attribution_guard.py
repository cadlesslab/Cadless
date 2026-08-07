#!/usr/bin/env python3
"""Attribution guard.

Commit messages and pull-request descriptions in this repository must not
carry AI-assistant attribution: no co-author trailer crediting an assistant,
no "generated with" footer. The product legitimately names its providers and
the ``claude-*`` model slugs throughout its own code and prose, so the
patterns here are deliberately narrow -- they match attribution boilerplate,
not the words on their own.

Fail-closed: any unexpected error is a failure, never a pass.

    python tools/attribution_guard.py [file ...]

Each argument is a file whose text is scanned; with no argument the text is
read from standard input. Exit code 0 means clean, 1 means findings (or an
internal error), 2 means bad usage.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Attribution signatures only. A bare provider or model name is deliberately
# absent: this repository ships an assistant-family provider and ``claude-*``
# model slugs as product code and describes them in prose, all of which must
# stay committable. Only boilerplate that credits the assistant is matched.
ATTRIBUTION_PATTERNS: dict[str, str] = {
    "assistant co-author trailer": r"(?im)^[ \t]*co-authored-by:[ \t]*claude\b",
    "assistant noreply address": r"(?i)noreply@anthropic\.com",
    "generated-with footer": r"(?i)generated with[ \t]*\[?claude code",
    "assistant tool url": r"(?i)claude\.(?:com/claude-code|ai/code)",
    "generated-with emoji footer": r"(?i)\U0001f916[ \t]*generated with",
}

_COMPILED = [(name, re.compile(rx)) for name, rx in ATTRIBUTION_PATTERNS.items()]


def scan(text: str) -> list[str]:
    """Return one finding line per attribution match in text."""
    findings: list[str] = []
    for name, rx in _COMPILED:
        for match in rx.finditer(text):
            line = text[: match.start()].count("\n") + 1
            snippet = text[max(0, match.start() - 10) : match.end() + 10].replace("\n", " ").strip()
            findings.append(f"[{name}] line {line}  ...{snippet}...")
    return findings


def _read(paths: list[str]) -> str:
    if not paths:
        return sys.stdin.read()
    return "\n".join(Path(p).read_text(encoding="utf8") for p in paths)


def run(paths: list[str]) -> bool:
    """Return True when clean. Any error counts as a failure (fail-closed)."""
    try:
        findings = scan(_read(paths))
    except Exception as exc:  # fail-closed: unscanned input is not clean input
        print(f"attribution guard errored -- treating as failure: {exc!r}", file=sys.stderr)
        return False

    if not findings:
        print("attribution guard passed -- no assistant attribution found")
        return True

    print(f"attribution guard failed -- {len(findings)} finding(s)", file=sys.stderr)
    for finding in findings:
        print(f"  {finding}", file=sys.stderr)
    print(
        "Remove the assistant co-author trailer / generated-with footer and try again.",
        file=sys.stderr,
    )
    return False


def main(argv: list[str]) -> int:
    return 0 if run(argv[1:]) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
