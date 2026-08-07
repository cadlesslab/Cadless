"""Domain registry tests (issue #20): catalog domains as data, not code.

Covers the registry module itself, manifest validation against it, and the
`GET /catalog` grouping the registry drives — including a synthetic third
domain registered by a test.
"""

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from cadless.catalog.domains import (
    BASE_METRICS,
    IR_METRICS,
    MESH_METRICS,
    Domain,
    all_domains,
    domain_label,
    domain_sort_key,
    find_domain,
    get_domain,
    register_domain,
    unregister_domain,
)
from cadless.catalog.ledger import Ledger
from cadless.catalog.loader import load_house
from cadless.catalog.manifest import load_manifest
from cadless.config import settings
from cadless.store import Store

# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _empty_catalog_root(tmp_path, monkeypatch):
    # The lifespan auto-load would inject the bundled samples into the hand-seeded
    # stores these tests assert exact contents of.
    monkeypatch.setattr(settings, "catalog_root", tmp_path / "no-catalog")


def _write_item(
    catalog_dir: Path,
    item_id: str,
    domain: str,
    *,
    name: str | None = None,
    geometry: dict | None = None,
    assertions: dict | None = None,
    artifacts: dict | None = None,
) -> Path:
    item = catalog_dir / item_id
    (item / "steps").mkdir(parents=True, exist_ok=True)
    (item / "steps" / "01.py").write_text("result = 1\n")
    step = {"index": 1, "instruction": "build it", "code": "steps/01.py"}
    if geometry:
        step["geometry"] = geometry
    if assertions:
        step["assertions"] = assertions
    if artifacts:
        step["artifacts"] = artifacts
    (item / "manifest.json").write_text(
        json.dumps(
            {
                "id": item_id,
                "name": name or item_id,
                "domain": domain,
                "steps": [step],
            }
        )
    )
    return item


@pytest.fixture
def garden():
    """A synthetic metre-authored domain with only the base metric set."""
    dom = register_domain(Domain(key="garden", label="Garden", authoring_units="m", sort_order=5))
    yield dom
    unregister_domain("garden")


# --------------------------------------------------------------------------- #
# registry basics
# --------------------------------------------------------------------------- #


def test_builtin_domains_registered():
    house = get_domain("house")
    mech = get_domain("mechanical")
    assert house.label == "House" and mech.label == "Mechanical"
    assert house.authoring_units == "m" and mech.authoring_units == "mm"


def test_export_scale_derived_from_authoring_units():
    assert get_domain("house").export_scale == 1000.0  # metres -> mm
    assert get_domain("mechanical").export_scale == 1.0


def test_builtin_eval_metric_sets():
    assert IR_METRICS <= get_domain("house").eval_metrics
    assert not (MESH_METRICS & get_domain("house").eval_metrics)
    assert MESH_METRICS <= get_domain("mechanical").eval_metrics
    assert not (IR_METRICS & get_domain("mechanical").eval_metrics)


def test_builtin_furniture_and_fixture_domains_registered():
    furniture = get_domain("furniture")
    fixture = get_domain("fixture")
    assert furniture.label == "Furniture"
    assert fixture.label == "Enclosures & Fixtures"
    assert furniture.authoring_units == "mm" and fixture.authoring_units == "mm"
    assert furniture.export_scale == 1.0 and fixture.export_scale == 1.0


def test_builtin_furniture_and_fixture_use_mech_style_metrics():
    for key in ("furniture", "fixture"):
        dom = get_domain(key)
        assert MESH_METRICS <= dom.eval_metrics
        assert BASE_METRICS <= dom.eval_metrics
        assert not (IR_METRICS & dom.eval_metrics)


def test_builtin_domain_ui_order():
    keys = [d.key for d in all_domains()]
    assert (
        keys.index("house")
        < keys.index("mechanical")
        < keys.index("furniture")
        < keys.index("fixture")
    )


def test_builtin_content_dirs_resolve_via_registry():
    assert get_domain("house").content_dir == "house-catalog"
    assert get_domain("mechanical").content_dir == "mech-catalog"
    assert get_domain("furniture").content_dir == "furniture-catalog"
    assert get_domain("fixture").content_dir == "fixture-catalog"


def test_domain_content_dir_defaults_to_key_catalog():
    assert Domain(key="thing", label="Thing").content_dir == "thing-catalog"
    assert Domain(key="thing", label="Thing", content_dir="things").content_dir == "things"


def test_settings_domain_catalog_dir_is_registry_driven(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "catalog_root", tmp_path)
    assert settings.domain_catalog_dir("furniture") == tmp_path / "furniture-catalog"
    assert settings.domain_catalog_dir("fixture") == tmp_path / "fixture-catalog"
    # the legacy per-domain properties delegate to the same generic resolver
    assert settings.house_catalog_dir == settings.domain_catalog_dir("house")
    assert settings.mech_catalog_dir == settings.domain_catalog_dir("mechanical")


def test_settings_domain_catalog_dir_unknown_domain_raises():
    with pytest.raises(ValueError, match="registered"):
        settings.domain_catalog_dir("bogus")


def test_get_domain_unknown_raises_with_known_keys():
    with pytest.raises(ValueError, match="house"):
        get_domain("bogus")
    assert find_domain("bogus") is None


def test_register_duplicate_key_raises():
    with pytest.raises(ValueError, match="house"):
        register_domain(Domain(key="house", label="Haus"))


def test_register_replace_overrides(garden):
    register_domain(Domain(key="garden", label="Garten", authoring_units="m"), replace=True)
    assert get_domain("garden").label == "Garten"


def test_invalid_authoring_units_rejected():
    with pytest.raises(ValueError, match="authoring_units"):
        Domain(key="weird", label="Weird", authoring_units="furlong")


def test_all_domains_sorted_by_sort_order(garden):
    keys = [d.key for d in all_domains()]
    assert keys.index("house") < keys.index("garden") < keys.index("mechanical")


def test_domain_label_falls_back_for_unregistered_key():
    assert domain_label("house") == "House"
    assert domain_label("legacy") == "Legacy"


def test_domain_sort_key_puts_unregistered_last():
    assert sorted(["legacy", "mechanical", "house"], key=domain_sort_key) == [
        "house",
        "mechanical",
        "legacy",
    ]


# --------------------------------------------------------------------------- #
# manifest/loader validation
# --------------------------------------------------------------------------- #


def test_load_manifest_rejects_unregistered_domain(tmp_path):
    item = _write_item(tmp_path, "x1", "not-a-domain")
    with pytest.raises(ValueError, match="not-a-domain"):
        load_manifest(item)


def test_load_manifest_accepts_registered_synthetic_domain(tmp_path, garden):
    item = _write_item(tmp_path, "gnome", "garden")
    assert load_manifest(item).domain == "garden"


# --------------------------------------------------------------------------- #
# worker child: scale applies to exports only, never the geometry summary
# --------------------------------------------------------------------------- #

_CHILD_STUB = """
class _Size:
    X, Y, Z = 1.0, 2.0, 3.0

class _BBox:
    size = _Size()

class _Shape:
    volume = 6.0
    is_manifold = True
    def bounding_box(self):
        return _BBox()
    def solids(self):
        return [self]
    def scale(self, factor):
        return ("scaled", factor)

result = _Shape()
"""


def _run_child(tmp_path, monkeypatch, capsys, extra_argv):
    from cadless import _worker_child, exporters

    seen: dict = {}

    def fake_export(shape, out_dir, name="model"):
        seen["shape"] = shape
        return str(Path(out_dir) / f"{name}.fake")

    monkeypatch.setattr(exporters, "EXPORTERS", {"fake": fake_export})
    code_file = tmp_path / "m.py"
    code_file.write_text(_CHILD_STUB)
    rc = _worker_child.main(["prog", str(code_file), str(tmp_path), *extra_argv])
    out = capsys.readouterr().out
    payload = json.loads(out.split(_worker_child.SENTINEL, 1)[1])
    return rc, payload, seen


def test_child_scales_exported_shape_only(tmp_path, monkeypatch, capsys):
    rc, payload, seen = _run_child(tmp_path, monkeypatch, capsys, ["1000"])
    assert rc == 0 and payload["ok"]
    assert seen["shape"] == ("scaled", 1000.0)
    assert payload["volume"] == 6.0  # geometry summary is unscaled
    assert payload["bbox"] == [1.0, 2.0, 3.0]


def test_child_identity_scale_exports_original_shape(tmp_path, monkeypatch, capsys):
    rc, payload, seen = _run_child(tmp_path, monkeypatch, capsys, [])
    assert rc == 0 and payload["ok"]
    assert not isinstance(seen["shape"], tuple)  # the shape itself, unscaled


# --------------------------------------------------------------------------- #
# GET /catalog: labels + ordering from the registry
# --------------------------------------------------------------------------- #


def _store_and_ledger(tmp_path):
    store = Store(db_path=tmp_path / "db.sqlite", artifacts_dir=tmp_path / "arts")
    ledger = Ledger(tmp_path / "catalog-ledger.json")
    return store, ledger


def test_catalog_groups_unregistered_domain_falls_back(tmp_path):
    store, ledger = _store_and_ledger(tmp_path)

    async def go():
        await store.init()
        await load_house(store, ledger, _write_item(tmp_path / "cat", "h1", "house", name="Casa"))
        # simulate a legacy ledger entry whose domain is no longer registered
        await store.create_project("Old Thing", catalog_item_id="old-1")
        ledger.record("old-1", 1, name="Old Thing", domain="legacy")

    asyncio.run(go())
    with TestClient(create_app(store=store)) as c:
        groups = c.get("/catalog").json()["groups"]
    assert [g["domain"] for g in groups] == ["house", "legacy"]
    assert groups[1]["label"] == "Legacy"  # graceful fallback, sorted last


def test_registered_domain_reaches_catalog_with_its_label_and_position(tmp_path, garden):
    """A domain the registry knows shows up in /catalog where the registry says.

    The wider version of this used to bake and score the item on the way past;
    that half moved to the authoring pipeline. What is asserted here needs
    neither — the point is the wiring from registry to API response, which is a
    separate thing from the registry assertions above.
    """
    store, ledger = _store_and_ledger(tmp_path)

    async def go():
        await store.init()
        cat = tmp_path / "cat"
        await load_house(store, ledger, _write_item(cat, "h1", "house", name="Casa"))
        await load_house(store, ledger, _write_item(cat, "g1", "garden", name="Trellis"))
        await load_house(store, ledger, _write_item(cat, "m1", "mechanical", name="Bracket"))

    asyncio.run(go())
    with TestClient(create_app(store=store)) as c:
        groups = c.get("/catalog").json()["groups"]
    # sort_order from the registry, not insertion or alphabetical order
    assert [g["domain"] for g in groups] == ["house", "garden", "mechanical"]
    assert groups[1]["label"] == "Garden"


def test_furniture_and_fixture_group_in_registry_order(tmp_path):
    """The two later builtin domains resolve their dirs and order like the rest."""
    store, ledger = _store_and_ledger(tmp_path)

    async def go():
        await store.init()
        cat = tmp_path / "cat"
        for item_id, domain, name in (
            ("h1", "house", "Casa"),
            ("m1", "mechanical", "Bracket"),
            ("f1", "furniture", "Stool"),
            ("x1", "fixture", "Clamp"),
        ):
            await load_house(store, ledger, _write_item(cat, item_id, domain, name=name))

    asyncio.run(go())
    with TestClient(create_app(store=store)) as c:
        groups = c.get("/catalog").json()["groups"]
    assert [g["domain"] for g in groups] == ["house", "mechanical", "furniture", "fixture"]
    assert [g["label"] for g in groups][2:] == ["Furniture", "Enclosures & Fixtures"]
