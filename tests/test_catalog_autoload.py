"""Startup auto-load of the catalog on this machine.

The tool must show its bundled sample parts *before* any API key is entered, but
the catalog is surfaced only after being loaded into the store and nothing loaded
it at startup — so the keyless experience came up empty. This pins that
create_app()'s lifespan auto-loads every registered domain's catalog from
settings.catalog_root, idempotently and non-fatally.

Received items are loaded here too, from the separate root they are written to.
There is no watcher and no scan loop: startup is the only thing that reads the
catalog off disk, so a root it does not walk is a root whose items disappear at
the next restart.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from cadless.catalog.importer import imported_domain_dir
from cadless.config import settings
from cadless.store import Store

BUNDLED_CATALOG = Path(__file__).resolve().parents[1] / "catalog"


def _bundled_item_count() -> int:
    """Items present on disk, so the expectation follows the tree instead of a
    magic number that goes stale every time content is added."""
    return len(list(BUNDLED_CATALOG.glob("*/*/manifest.json")))


@pytest.fixture
def bundled_root(monkeypatch):
    monkeypatch.setattr(settings, "catalog_root", BUNDLED_CATALOG)
    return BUNDLED_CATALOG


def test_startup_autoloads_bundled_catalog(tmp_path, bundled_root):
    store = Store(db_path=tmp_path / "db.sqlite", artifacts_dir=tmp_path / "arts")
    with TestClient(create_app(store=store)) as client:
        # One page wide enough for the whole bundled catalog, so the name check
        # below cannot start failing because an item fell off the first page.
        out = client.get("/catalog", params={"limit": 200}).json()
    # Every item on disk must arrive. `_autoload_catalog` swallows a failing domain
    # so a bad item cannot block boot, which means a floor here would stay green
    # while most of the catalog quietly failed to load.
    assert out["total"] == _bundled_item_count(), out["total"]
    # Read the domains off the facet list, which is computed over every live item
    # rather than over the returned page.
    assert {facet["key"] for facet in out["domains"]} == {
        "house",
        "mechanical",
        "furniture",
        "fixture",
    }, out["domains"]
    names = {it["name"] for it in out["items"]}
    assert {"Flat Washer", "L-Bracket", "Mounting Plate"} <= names, names


def test_autoload_is_idempotent(tmp_path, bundled_root):
    store = Store(db_path=tmp_path / "db.sqlite", artifacts_dir=tmp_path / "arts")
    with TestClient(create_app(store=store)) as client:
        first = client.get("/catalog").json()["total"]
    # A second boot on the same persisted store must not duplicate the items.
    with TestClient(create_app(store=store)) as client:
        second = client.get("/catalog").json()["total"]
    assert first == _bundled_item_count(), first
    assert second == first, (first, second)


def _write_received_item(root: Path, item_id: str = "l-bracket") -> Path:
    """One item where an import puts it, without going through the importer.

    What is under test is the walk, not the write: the importer has its own
    tests, and going through it here would make this pass or fail for reasons
    that live somewhere else.
    """
    item = root / item_id
    (item / "steps").mkdir(parents=True)
    (item / "steps" / "01.py").write_text("from build123d import Box\n\nresult = Box(1, 1, 1)\n")
    (item / "manifest.json").write_text(
        json.dumps(
            {
                "id": item_id,
                "name": "L Bracket",
                "domain": "mechanical",
                "steps": [{"index": 1, "instruction": "A bracket.", "code": "steps/01.py"}],
            }
        )
    )
    return item


@pytest.fixture
def empty_roots(monkeypatch, tmp_path):
    """A machine with nothing bundled, so only what was received can show up."""
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(settings, "catalog_root", tmp_path / "catalog")


def test_startup_autoloads_what_was_received(tmp_path, empty_roots):
    # Imports do not land with the bundled catalog — that ships with the image
    # and the deployment mounts it read-only. A startup that walked only the
    # bundled root would drop every imported item at the next boot.
    _write_received_item(imported_domain_dir("mechanical"))

    store = Store(db_path=tmp_path / "db.sqlite", artifacts_dir=tmp_path / "arts")
    with TestClient(create_app(store=store)) as client:
        out = client.get("/catalog").json()

    assert [item["name"] for item in out["items"]] == ["L Bracket"], out


def test_one_unreadable_item_does_not_hide_the_rest_of_its_root(tmp_path, empty_roots):
    # `discover_houses` counts any directory holding a manifest, and the walk
    # had no per-item guard — so one item this version cannot read took every
    # other item in that directory down with it. That was survivable while every
    # item was authored here; an imported one is written by whoever sent it.
    received = imported_domain_dir("mechanical")
    _write_received_item(received, "good")
    (received / "broken").mkdir(parents=True)
    (received / "broken" / "manifest.json").write_text("{")

    store = Store(db_path=tmp_path / "db.sqlite", artifacts_dir=tmp_path / "arts")
    with TestClient(create_app(store=store)) as client:
        out = client.get("/catalog").json()

    assert [item["name"] for item in out["items"]] == ["L Bracket"], out


def test_a_broken_bundled_item_does_not_cost_the_received_ones(tmp_path, empty_roots):
    # The two roots are walked in the same pass. A bundled item that fails to
    # parse must not take the imports down with it: they are the user's own
    # data, and losing them to someone else's broken file is not a trade.
    (settings.catalog_root / "mech-catalog" / "broken").mkdir(parents=True)
    (settings.catalog_root / "mech-catalog" / "broken" / "manifest.json").write_text("{")
    _write_received_item(imported_domain_dir("mechanical"))

    store = Store(db_path=tmp_path / "db.sqlite", artifacts_dir=tmp_path / "arts")
    with TestClient(create_app(store=store)) as client:
        out = client.get("/catalog").json()

    assert [item["name"] for item in out["items"]] == ["L Bracket"], out
