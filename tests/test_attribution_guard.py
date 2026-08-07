"""Tests for the attribution guard (tools/attribution_guard.py).

The guard scans commit messages and pull-request bodies, never the source
tree, so unlike the leak guard's tests these samples can be written out in
full without the guard matching this file.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tools.attribution_guard import scan

_ROOT = Path(__file__).resolve().parent.parent
_GUARD = _ROOT / "tools" / "attribution_guard.py"

FLAGGED = {
    "co-author trailer": "feat: a thing\n\nCo-authored-by: Claude Fable 5 <noreply@anthropic.com>\n",
    "co-author trailer only": "fix: y\n\nCo-Authored-By: Claude <someone@example.com>\n",
    "generated-with footer": "Summary\n\nGenerated with Claude Code\n",
    "generated-with link": "Body\n\ngenerated with [Claude Code](https://claude.com/claude-code)\n",
    "tool url alone": "see https://claude.ai/code for details",
    "emoji footer": "Body\n\n\U0001f916 Generated with something\n",
}

# Product code and prose name the provider and the model slugs; none of this
# is attribution and none of it may be flagged.
ALLOWED = {
    "provider default note": (
        "fix: default to Anthropic and correct three stale notes\n\n"
        "CADLESS_LLM_PROVIDER=anthropic is now the default.\n"
    ),
    "model slug passthrough": "feat: map claude-sonnet-4-6 in the provider registry\n",
    "human co-author": "feat: y\n\nCo-authored-by: ph22why <ph22why@gmail.com>\n",
    "prose about the model": "docs: explain that claude-* slugs pass through untouched\n",
    "signed off only": "chore: tidy\n\nSigned-off-by: A Dev <dev@example.com>\n",
}


def test_flags_every_attribution_sample():
    for label, text in FLAGGED.items():
        assert scan(text), f"expected {label!r} to be flagged"


def test_allows_every_legitimate_sample():
    for label, text in ALLOWED.items():
        assert scan(text) == [], f"{label!r} must not be flagged, got {scan(text)}"


def test_clean_text_has_no_findings():
    assert scan("just an ordinary commit message\n") == []


def test_cli_fails_on_attribution(tmp_path):
    bad = tmp_path / "msg.txt"
    bad.write_text(FLAGGED["co-author trailer"], encoding="utf8")
    result = subprocess.run([sys.executable, str(_GUARD), str(bad)], capture_output=True)
    assert result.returncode == 1


def test_cli_passes_on_clean_input(tmp_path):
    good = tmp_path / "msg.txt"
    good.write_text(ALLOWED["provider default note"], encoding="utf8")
    result = subprocess.run([sys.executable, str(_GUARD), str(good)], capture_output=True)
    assert result.returncode == 0
