"""Pin the declarations of the artifact-kind vocabulary to each other.

One vocabulary declared in several places, in two languages: ``EXPORTERS`` says what a
build can be exported to, ``_MEDIA`` says how each kind is served, ``ArtifactKind``
says what the browser can name, and ``FORMAT_META``/``ORDER`` say how the export
menu labels and orders them. Nothing compared them, and each way of drifting is
quiet in its own way:

* a kind in ``EXPORTERS`` and not ``_MEDIA`` is written, stored, and then refused
  at the download route -- at request time, not at test time;
* a kind in ``EXPORTERS`` and not ``ArtifactKind`` is invisible to the browser;
* a kind in ``ArtifactKind`` and not ``ORDER`` type-checks and still never
  appears, because the menu renders that list rather than the union.

``_MEDIA`` is deliberately *not* required to equal the rest. ``thumbnail`` and
``guide`` are served but never exported: they are pictures the engine made about
a build rather than formats the build was converted to, and keeping them out of
``EXPORTERS`` is what keeps them out of the export menu and out of the per-part
``model_p*`` naming that ``backend.artifact_io.exported_parts`` reads. So the
containment below is the contract, and an equality here would forbid the design.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from backend.routers.artifacts import _MEDIA
from cadless.exporters import EXPORTERS

_FRONTEND = Path(__file__).resolve().parents[1] / "frontend" / "src"
_API_TS = _FRONTEND / "api.ts"
_FORMATS_TS = _FRONTEND / "panels" / "exportFormats.ts"


def _source(path: Path) -> str:
    if not path.exists():  # pragma: no cover - only in a checkout without the frontend
        pytest.skip(f"{path} is not present in this checkout")
    return path.read_text(encoding="utf-8")


def _ts_artifact_kinds(src: str) -> set[str]:
    match = re.search(r"export\s+type\s+ArtifactKind\s*=(.*?);", src, re.DOTALL)
    assert match, "frontend/src/api.ts declares no `export type ArtifactKind`"
    return set(re.findall(r"[\"']([^\"']+)[\"']", match.group(1)))


def _ts_format_meta_keys(src: str) -> set[str]:
    match = re.search(r"export\s+const\s+FORMAT_META[^=]*=\s*\{(.*?)\n\}", src, re.DOTALL)
    assert match, "frontend/src/panels/exportFormats.ts declares no `FORMAT_META`"
    return set(re.findall(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:", match.group(1), re.MULTILINE))


def _ts_order(src: str) -> set[str]:
    match = re.search(r"const\s+ORDER\s*:[^=]*=\s*\[(.*?)\]", src, re.DOTALL)
    assert match, "frontend/src/panels/exportFormats.ts declares no `ORDER`"
    return set(re.findall(r"[\"']([^\"']+)[\"']", match.group(1)))


def test_the_browser_names_exactly_the_kinds_a_build_exports_to():
    python_kinds = set(EXPORTERS)
    ts_kinds = _ts_artifact_kinds(_source(_API_TS))

    assert python_kinds == ts_kinds, (
        "the artifact kinds have drifted between cadless/exporters.py and "
        f"frontend/src/api.ts. only in Python: {sorted(python_kinds - ts_kinds)}; "
        f"only in TypeScript: {sorted(ts_kinds - python_kinds)}"
    )


def test_every_exported_kind_has_a_media_type():
    missing = set(EXPORTERS) - set(_MEDIA)
    assert not missing, (
        f"these kinds are exported but have no media type: {sorted(missing)}. "
        "backend/routers/artifacts.py refuses a kind it cannot type, so the file "
        "would be written and stored and then never served."
    )


def test_a_kind_served_without_being_exported_is_allowed():
    # The other direction is open on purpose, and this is what says so: thumbnail
    # and guide are pictures of a build rather than formats it was converted to.
    # Were this to start failing, the containment above had been tightened into
    # an equality and the design went with it.
    served_only = set(_MEDIA) - set(EXPORTERS)
    assert served_only, (
        "no kind is served without being exported any more. If that is deliberate, "
        "this test should go; if it is not, a picture kind has been pulled into "
        "EXPORTERS and is now on the per-part naming path it does not fit."
    )


def test_the_export_menu_labels_and_orders_every_kind_it_can_show():
    src = _source(_FORMATS_TS)
    kinds = _ts_artifact_kinds(_source(_API_TS))

    assert _ts_format_meta_keys(src) == kinds, (
        "FORMAT_META and ArtifactKind disagree. TypeScript catches a missing key "
        "in the exhaustive Record, but not a stale extra one."
    )
    assert _ts_order(src) == kinds, (
        "ORDER and ArtifactKind disagree. This one type-checks either way: the "
        "menu renders ORDER, so a kind missing from it is simply never offered."
    )


def test_the_parsers_read_declarations_rather_than_prose():
    # Named cases, so a parser that silently matched nothing cannot pass every
    # assertion above by comparing two empty sets.
    assert "step" in _ts_artifact_kinds(_source(_API_TS))
    assert "glb" in _ts_format_meta_keys(_source(_FORMATS_TS))
    assert "stl" in _ts_order(_source(_FORMATS_TS))
    assert "guide" in _MEDIA
