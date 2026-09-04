"""Pin the two cross-language declarations of the chat block shape to each other.

``BlockKind`` and ``ContentBlock`` exist twice — once in ``cadless/llm/types.py``
as the seam's own types, and once in ``frontend/src/api.ts`` as what the browser
expects the transcript to contain. Nothing made them agree: they were kept in
step by convention, and by the time this test was written the TypeScript
``ContentBlock`` had already lost four fields that way.

The failure mode is quiet in both directions. A kind the backend can persist but
the frontend cannot name renders as nothing on reload; a field the API sends and
the interface omits is invisible to every consumer that trusts the type. Neither
shows up in a type check, because the two languages never meet.

The Python side is read from the live objects rather than from the source text —
``get_args`` and ``model_fields`` cannot drift from what the module actually
declares. Only the TypeScript side has to be parsed.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import get_args

import pytest

from cadless.llm.types import BlockKind, ContentBlock

_API_TS = Path(__file__).resolve().parents[1] / "frontend" / "src" / "api.ts"


def _ts_source() -> str:
    if not _API_TS.exists():  # pragma: no cover - only in a checkout without the frontend
        pytest.skip(f"{_API_TS} is not present in this checkout")
    return _API_TS.read_text(encoding="utf-8")


def _ts_block_kinds(src: str) -> set[str]:
    """The string members of the ``BlockKind`` union, however it is wrapped."""
    match = re.search(r"export\s+type\s+BlockKind\s*=(.*?);", src, re.DOTALL)
    assert match, "frontend/src/api.ts declares no `export type BlockKind`"
    return set(re.findall(r"[\"']([^\"']+)[\"']", match.group(1)))


def _ts_content_block_fields(src: str) -> set[str]:
    """The property names of the ``ContentBlock`` interface."""
    match = re.search(r"export\s+interface\s+ContentBlock\s*\{(.*?)\n\}", src, re.DOTALL)
    assert match, "frontend/src/api.ts declares no `export interface ContentBlock`"
    body = match.group(1)
    # Strip comments so a documented field name inside prose is not counted.
    body = re.sub(r"//[^\n]*", "", body)
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.DOTALL)
    return set(re.findall(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*\??\s*:", body, re.MULTILINE))


def test_block_kind_literal_matches_the_typescript_union():
    python_kinds = set(get_args(BlockKind))
    ts_kinds = _ts_block_kinds(_ts_source())

    assert python_kinds == ts_kinds, (
        "BlockKind has drifted between cadless/llm/types.py and frontend/src/api.ts. "
        f"only in Python: {sorted(python_kinds - ts_kinds)}; "
        f"only in TypeScript: {sorted(ts_kinds - python_kinds)}"
    )


def test_content_block_fields_match_the_typescript_interface():
    python_fields = set(ContentBlock.model_fields)
    ts_fields = _ts_content_block_fields(_ts_source())

    assert python_fields == ts_fields, (
        "ContentBlock has drifted between cadless/llm/types.py and frontend/src/api.ts. "
        f"only in Python: {sorted(python_fields - ts_fields)}; "
        f"only in TypeScript: {sorted(ts_fields - python_fields)}"
    )


def _ts_image_limits(src: str) -> dict[str, object]:
    """The values of the ``IMAGE_LIMITS`` object literal."""
    match = re.search(r"export\s+const\s+IMAGE_LIMITS\s*=\s*\{(.*?)\n\}", src, re.DOTALL)
    assert match, "frontend/src/api.ts declares no `export const IMAGE_LIMITS`"
    body = re.sub(r"//[^\n]*", "", match.group(1))

    def number(key: str) -> int:
        m = re.search(rf"\b{key}\s*:\s*([\d_]+)", body)
        assert m, f"IMAGE_LIMITS has no numeric `{key}`"
        return int(m.group(1).replace("_", ""))

    types = re.search(r"\bmediaTypes\s*:\s*\[(.*?)\]", body, re.DOTALL)
    assert types, "IMAGE_LIMITS has no `mediaTypes` array"
    return {
        "maxBytes": number("maxBytes"),
        "maxTurnBytes": number("maxTurnBytes"),
        "maxCount": number("maxCount"),
        "mediaTypes": re.findall(r"[\"']([^\"']+)[\"']", types.group(1)),
    }


def test_the_composers_limits_are_the_servers_limits():
    """The client copy of each limit must be the server's, or it is worse than none.

    The composer refuses an attachment before uploading it, which is only a
    kindness while the two agree. Let them drift and the browser starts refusing
    turns the server would have taken, or waving through ones it will not — and
    both suites stay green, because each side asserts its own copy.
    """
    from cadless.config import settings

    limits = _ts_image_limits(_ts_source())

    assert limits["maxBytes"] == settings.chat_image_max_bytes
    assert limits["maxTurnBytes"] == settings.chat_image_max_turn_bytes
    assert limits["maxCount"] == settings.chat_image_max_count
    assert limits["mediaTypes"] == list(settings.chat_image_media_types)


def test_the_image_kind_is_present_on_both_sides():
    """A named case, so a wholesale rewrite of either file cannot pass by emptying both.

    Set equality holds trivially if a parser change makes one side come back
    empty, and the two assertions above would go green on it.
    """
    assert "image" in set(get_args(BlockKind))
    assert "image" in _ts_block_kinds(_ts_source())
