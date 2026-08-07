"""Mechanical catalog end-to-end tests (Phase 1).

Exercises the committed hand-authored mechanical parts under
``benchmarks/mech-catalog`` through the shared, domain-agnostic catalog
infrastructure: discovery/manifest validation, the offline loader + ledger,
the ``--part`` CLI alias, and the backend grouping.

The loader never executes step code, so none of this needs build123d.
"""

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from cadless.catalog.cli import (
    _build_parser,
    _explicit_ids,
    _selected_houses,
    main,
)
from cadless.catalog.ledger import Ledger
from cadless.catalog.loader import clear_house, load_house
from cadless.catalog.manifest import discover_houses, load_manifest
from cadless.config import settings
from cadless.store import Store

# The multi-step part ladders now ship in-repo, so this runs against the bundled
# tree by default. The guard remains because settings.catalog_root is
# configurable: point it somewhere without these parts and the module skips
# rather than failing on absent content.
MECH_DIR = settings.mech_catalog_dir
pytestmark = pytest.mark.skipif(
    not (MECH_DIR / "flanged-shaft").exists(),
    reason=f"mech catalog absent: {MECH_DIR} (set CADLESS_CATALOG_ROOT)",
)

# The multi-step part ladders and their minimum length. The single-step sample
# parts bundled in the same directory are deliberately absent: these entries
# describe ladders, and a one-step item has none to measure.
EXPECTED_PARTS = {
    "bearing-block": 5,
    "connecting-rod": 7,
    "crankshaft": 8,
    "enclosure": 4,
    "engine-block": 7,
    "flanged-shaft": 4,
    "flywheel": 6,
    "hinge": 3,
    "l-mounting-bracket": 4,
    "piston": 6,
    "piston-assembly": 3,
    "spur-gear": 4,
    "v-pulley": 4,
}


def run(coro):
    return asyncio.run(coro)


def _store(tmp_path: Path) -> Store:
    return Store(db_path=tmp_path / "db.sqlite", artifacts_dir=tmp_path / "arts")


# --------------------------------------------------------------------------- #
# 1. Discovery + manifest validation
# --------------------------------------------------------------------------- #


def test_discovers_all_hand_authored_parts():
    found = discover_houses(MECH_DIR)
    assert set(found) >= set(EXPECTED_PARTS), found


@pytest.mark.parametrize("part_id", sorted(EXPECTED_PARTS))
def test_part_manifest_valid_and_mechanical(part_id):
    manifest = load_manifest(MECH_DIR / part_id)
    assert manifest.domain == "mechanical"
    assert manifest.id == part_id
    # contiguous steps from 1, at least the documented ladder length
    indices = [s.index for s in manifest.steps]
    assert indices == list(range(1, len(indices) + 1))
    assert len(manifest.steps) >= EXPECTED_PARTS[part_id]
    # every step carries baked geometry + an stl artifact + tolerances
    for step in manifest.steps:
        assert step.geometry.volume is not None and step.geometry.volume > 0
        assert step.geometry.bbox is not None
        assert "stl" in step.artifacts
        assert "volume_tol" in step.assertions and "bbox_tol" in step.assertions
        assert (MECH_DIR / part_id / step.artifacts["stl"]).exists()


# --------------------------------------------------------------------------- #
# 2. Loader idempotency / ledger / clear (mechanical domain)
# --------------------------------------------------------------------------- #


def test_load_records_mechanical_domain_and_is_idempotent(tmp_path):
    async def go():
        s = _store(tmp_path)
        await s.init()
        led = Ledger(tmp_path / "ledger.json")
        part = MECH_DIR / "flanged-shaft"
        n_steps = len(load_manifest(part).steps)

        pid = await load_house(s, led, part)
        assert pid is not None
        entry = led.get("flanged-shaft")
        assert entry["domain"] == "mechanical"
        assert entry["step_count"] == n_steps

        versions = await s.list_versions(pid)
        assert len(versions) == n_steps
        assert versions[0].parent_version_id is None
        assert versions[-1].parent_version_id == versions[-2].id

        # idempotent: second load is skipped
        assert await load_house(s, led, part) is None
        assert len(await s.list_projects()) == 1

        # reload replaces; clear removes only this project
        pid2 = await load_house(s, led, part, reload=True)
        assert pid2 is not None and pid2 != pid
        assert await clear_house(s, led, "flanged-shaft") is True
        assert led.get("flanged-shaft") is None

    run(go())


# --------------------------------------------------------------------------- #
# 3. CLI --part alias
# --------------------------------------------------------------------------- #


def test_part_alias_equivalent_to_house():
    parser = _build_parser()
    a = parser.parse_args(["load", "--part", "flanged-shaft"])
    b = parser.parse_args(["load", "--house", "flanged-shaft"])
    assert _explicit_ids(a) == _explicit_ids(b) == ["flanged-shaft"]
    # --part and --house combine
    c = parser.parse_args(["clear", "--house", "a", "--part", "b"])
    assert _explicit_ids(c) == ["a", "b"]
    # --all expands to all discovered ids
    d = parser.parse_args(["load", "--all", "--catalog-dir", str(MECH_DIR)])
    assert set(_selected_houses(d, str(MECH_DIR))) >= set(EXPECTED_PARTS)


def test_cli_list_lists_parts(capsys, tmp_path):
    # isolate the ledger so nothing shows as loaded
    rc = main(["list", "--catalog-dir", str(MECH_DIR)], ledger=Ledger(tmp_path / "ledger.json"))
    assert rc == 0
    out = capsys.readouterr().out
    for part_id in EXPECTED_PARTS:
        assert part_id in out


# --------------------------------------------------------------------------- #
# 5. Backend grouping under the Mechanical label
# --------------------------------------------------------------------------- #


def test_catalog_endpoint_groups_mechanical(tmp_path):
    store = _store(tmp_path)
    ledger = Ledger(tmp_path / "catalog-ledger.json")

    async def go():
        await store.init()
        await load_house(store, ledger, MECH_DIR / "flanged-shaft")
        await load_house(store, ledger, MECH_DIR / "l-mounting-bracket")

    run(go())

    with TestClient(create_app(store=store)) as c:
        body = c.get("/catalog").json()
    mech = [g for g in body["groups"] if g["domain"] == "mechanical"]
    assert len(mech) == 1
    assert mech[0]["label"] == "Mechanical"
    names = {it["name"] for it in mech[0]["items"]}
    # Names come from the (re-authored) manifests, so derive the expectation from
    # them rather than hard-coding — keeps this green across catalog re-authoring.
    expected = {
        load_manifest(MECH_DIR / "flanged-shaft").name,
        load_manifest(MECH_DIR / "l-mounting-bracket").name,
    }
    assert expected <= names
    assert all(it["current_version_id"] is not None for it in mech[0]["items"])


def test_manifests_are_json_serialisable():
    # guards against non-serialisable values sneaking into committed manifests
    for part_id in EXPECTED_PARTS:
        raw = json.loads((MECH_DIR / part_id / "manifest.json").read_text())
        assert raw["domain"] == "mechanical"
