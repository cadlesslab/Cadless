"""The published tree reads in one language.

This repository is public and its prose is English. Korean has leaked in twice
now — once as a whole ADR paragraph, and once as a fragment of a TODO line that
survived a partial hand-translation — and in both cases nothing failed, so it
was only noticed by someone reading. That is what this test replaces.

It is deliberately *not* part of `tools/leak_guard.py`. That guard has no
allowlist by design, because a leaked credential is not something to grant a
budget to. This rule does need a scope: a couple of tests use Hangul as a
functional fixture, since one Hangul character is three UTF-8 bytes and that is
the cheapest way to build a path segment of an exact byte length. Folding an
exception list into the leak guard would weaken the property that makes it
trustworthy, so the two live apart.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def _load_guard():
    spec = importlib.util.spec_from_file_location("leak_guard", _ROOT / "tools" / "leak_guard.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


leak_guard = _load_guard()

# Hangul syllables plus every Jamo block, so a decomposed form cannot slip past.
#
# Written as escapes rather than as the characters themselves: spelled out, the
# range endpoints are Hangul, and this file would be its own first offender —
# the same self-matching trap `tools/leak_guard.py` documents for its patterns.
HANGUL = re.compile(
    "["
    "\u3131-\u318e"  # Hangul Compatibility Jamo
    "\u1100-\u11ff"  # Hangul Jamo
    "\ua960-\ua97c"  # Hangul Jamo Extended-A
    "\uac00-\ud7a3"  # Hangul Syllables
    "\ud7b0-\ud7fb"  # Hangul Jamo Extended-B
    "]"
)

# The only files allowed to contain Hangul, and the reason each one may. These
# use it as data, not as prose: one Hangul character encodes to three UTF-8
# bytes, which is how these tests build path segments of an exact byte length
# and check the multibyte boundary handling of the .cls reader.
FUNCTIONAL_FIXTURES = {
    Path("tests/test_catalog_pack.py"),
    Path("tests/test_catalog_unpack.py"),
}


def _scannable():
    """Every file the leak guard would read, using its own scope definition."""
    for path, text in leak_guard.iter_text_files(_ROOT):
        yield path.relative_to(_ROOT), text


def test_no_korean_in_the_published_tree():
    offenders: dict[Path, list[str]] = {}
    for rel, text in _scannable():
        if rel in FUNCTIONAL_FIXTURES:
            continue
        lines = [
            f"{rel}:{i}: {line.strip()[:120]}"
            for i, line in enumerate(text.splitlines(), 1)
            if HANGUL.search(line)
        ]
        if lines:
            offenders[rel] = lines

    assert not offenders, "Korean text in the published tree:\n" + "\n".join(
        line for lines in offenders.values() for line in lines
    )


def test_the_fixture_exemption_stays_honest():
    """An exemption nobody re-checks becomes a hole.

    If a listed file stops needing Hangul, the entry must go — otherwise the
    list grows into the allowlist this rule was split out to avoid.
    """
    scanned = dict(_scannable())
    for rel in FUNCTIONAL_FIXTURES:
        assert rel in scanned, f"{rel} is exempted but no longer scanned — drop the entry"
        assert HANGUL.search(scanned[rel]), (
            f"{rel} is exempted but contains no Hangul any more — drop the entry"
        )
