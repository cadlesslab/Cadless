"""Public overlay assets stay in step with the bundled catalog.

CREDITS.md ships in the public tree and must record the provenance of every
bundled catalog item — an item whose origin is not recorded does not ship.

Two things make this a gate rather than a formality. It sweeps the tree instead
of the domain registry, so content parked under an unregistered directory still
faces it. And it checks what the provenance record *says*, not merely that it
says something: a non-empty licence string is not the same as a licence this
repository may redistribute under.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_CATALOG = _ROOT / "catalog"

# The only provenance a bundled item may declare. Everything here is an original
# design authored for this project; an item drawn from a third-party CAD corpus
# does not belong in this tree at all, whatever its licence permits.
_REQUIRED_DATASET = "cadless-samples"
_REQUIRED_LICENSE = "MIT"

# Per-domain floors rather than one total: a global count stays green while an
# entire domain disappears and another grows, which is the drift worth catching.
_MIN_ITEMS_PER_DIR = {
    "mech-catalog": 18,
    "furniture-catalog": 10,
    "fixture-catalog": 10,
    "house-catalog": 1,
}


def _item_dirs() -> list[Path]:
    """Every bundled item directory, swept from the tree itself.

    One level deep, matching ``discover_houses`` — an item is a directory holding
    a ``manifest.json``.
    """
    return sorted(manifest.parent for manifest in _CATALOG.glob("*/*/manifest.json"))


def _source_record(item_dir: Path) -> dict:
    source = item_dir / "source.json"
    assert source.exists(), f"{item_dir.name}: missing source.json"
    record = json.loads(source.read_text())
    assert record.get("id"), f"{item_dir.name}: source.json has no id"
    return record


def test_every_domain_keeps_its_bundled_items():
    counts = Counter(item_dir.parent.name for item_dir in _item_dirs())
    for content_dir, minimum in _MIN_ITEMS_PER_DIR.items():
        assert counts[content_dir] >= minimum, (content_dir, counts[content_dir], minimum)


def test_bundled_catalog_items_have_recorded_sources():
    for item_dir in _item_dirs():
        item = _source_record(item_dir)
        assert item.get("author"), f"{item['id']}: missing author"
        assert item.get("dataset") == _REQUIRED_DATASET, (item["id"], item.get("dataset"))
        assert item.get("license") == _REQUIRED_LICENSE, (item["id"], item.get("license"))


def test_credits_lists_every_bundled_item():
    # Overlay location in the private repo; repo root in the public tree.
    overlay = _ROOT / "tools" / "public-assets" / "CREDITS.md"
    credits = (overlay if overlay.exists() else _ROOT / "CREDITS.md").read_text()
    for item_dir in _item_dirs():
        item_id = _source_record(item_dir)["id"]
        assert f"`{item_id}`" in credits, f"CREDITS.md bundled-catalog list is missing {item_id!r}"
