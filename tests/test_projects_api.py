"""Project CRUD API tests."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from cadless.config import settings
from cadless.store import Store
from tests.catalog_helpers import load_catalog_item


@pytest.fixture
def store(tmp_path, monkeypatch):
    # The lifespan auto-loads any bundled catalog into the store; these tests assert
    # exact store contents, so boot the app on an empty catalog root. `data_dir` is
    # the other root it walks (received items live there), and a developer with one
    # imported locally would otherwise have it show up in these stores.
    monkeypatch.setattr(settings, "catalog_root", tmp_path / "no-catalog")
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    return Store(db_path=tmp_path / "db.sqlite", artifacts_dir=tmp_path / "artifacts")


@pytest.fixture
def client(store):
    with TestClient(create_app(store=store)) as c:
        yield c


def test_create_list_get(client):
    r = client.post("/projects", json={"name": "Bracket"})
    assert r.status_code == 201
    pid = r.json()["id"]
    assert r.json()["name"] == "Bracket"

    assert [p["id"] for p in client.get("/projects").json()] == [pid]
    assert client.get(f"/projects/{pid}").json()["name"] == "Bracket"


def test_rename(client):
    pid = client.post("/projects", json={"name": "A"}).json()["id"]
    r = client.patch(f"/projects/{pid}", json={"name": "B"})
    assert r.status_code == 200 and r.json()["name"] == "B"


def test_delete(client):
    pid = client.post("/projects", json={"name": "A"}).json()["id"]
    assert client.delete(f"/projects/{pid}").status_code == 204
    assert client.get(f"/projects/{pid}").status_code == 404


def test_404_paths(client):
    assert client.get("/projects/999").status_code == 404
    assert client.patch("/projects/999", json={"name": "x"}).status_code == 404
    assert client.delete("/projects/999").status_code == 404


def test_invalid_body_422(client):
    assert client.post("/projects", json={"name": ""}).status_code == 422
    assert client.post("/projects", json={}).status_code == 422


def test_is_catalog_flag_distinguishes_catalog_from_user_projects(tmp_path):
    """GET /projects[...] marks catalog-loaded projects so the UI can lock editing."""
    import asyncio
    import json
    from pathlib import Path

    from cadless.catalog.ledger import Ledger
    from cadless.catalog.loader import load_house

    store = Store(db_path=tmp_path / "db.sqlite", artifacts_dir=tmp_path / "arts")
    house = tmp_path / "cat" / "deepcad-1"
    (house / "steps").mkdir(parents=True)
    (house / "steps" / "01.py").write_text("result = 1\n")
    (house / "manifest.json").write_text(
        json.dumps(
            {
                "id": "deepcad-1",
                "name": "Cube Part",
                "domain": "mechanical",
                "steps": [{"index": 1, "instruction": "s1", "code": "steps/01.py"}],
            }
        )
    )
    ledger = Ledger(Path(store.db_path).parent / "catalog-ledger.json")

    async def go():
        await store.init()
        return await load_house(store, ledger, house)

    cat_pid = asyncio.run(go())

    with TestClient(create_app(store=store)) as c:
        user_pid = c.post("/projects", json={"name": "Mine"}).json()["id"]
        # Single-project GET carries the flag both ways.
        assert c.get(f"/projects/{cat_pid}").json()["is_catalog"] is True
        assert c.get(f"/projects/{user_pid}").json()["is_catalog"] is False
        # And the list view agrees.
        flags = {p["id"]: p["is_catalog"] for p in c.get("/projects").json()}
        assert flags[cat_pid] is True and flags[user_pid] is False


def test_clone_project(client):
    pid = client.post("/projects", json={"name": "House"}).json()["id"]
    r = client.post(f"/projects/{pid}/clone", json={"name": "House (copy)"})
    assert r.status_code == 201
    clone = r.json()
    assert clone["id"] != pid and clone["name"] == "House (copy)"
    # both projects are listed
    ids = [p["id"] for p in client.get("/projects").json()]
    assert pid in ids and clone["id"] in ids


def test_clone_missing_404(client):
    assert client.post("/projects/9999/clone", json={}).status_code == 404


def test_rename_rejects_catalog_item_403(client, store, tmp_path):
    """Renaming a catalog item splits the two views of it in half: /catalog
    prefers the ledger name while /projects shows the new one."""
    pid = load_catalog_item(store, tmp_path / "cat")
    before = client.get(f"/projects/{pid}").json()["name"]

    r = client.patch(f"/projects/{pid}", json={"name": "Renamed"})

    assert r.status_code == 403
    # Cloning is the way out of this one, and the refusal has to say so.
    assert r.json()["detail"] == "Catalog items are read-only. Clone the item to rename it."
    assert client.get(f"/projects/{pid}").json()["name"] == before


def test_delete_rejects_catalog_item_403(client, store, tmp_path):
    """Deleting a catalog item cannot be undone. `delete_project` cascades the
    versions away and rmtrees their artifacts, and the ledger entry it leaves
    behind makes the next load skip the item — a restart does not bring it back.
    """
    pid = load_catalog_item(store, tmp_path / "cat")

    r = client.delete(f"/projects/{pid}")

    assert r.status_code == 403
    # Not "clone it": a copy is no way to delete the original, so this one has to
    # point at the catalog instead. That sentence is the whole reason the shared
    # refusal takes a remedy.
    assert r.json()["detail"] == (
        "Catalog items are read-only. Clear it from the catalog to remove it."
    )
    assert client.get(f"/projects/{pid}").status_code == 200


def test_a_deleted_catalog_project_reads_as_gone_not_as_protected(client, store, tmp_path):
    """Deleting a project does not prune the ledger, so the entry outlives it and
    the read-only check would still recognise the id. 404 is decided first, the
    way the other gated routes guarantee — a project that is gone is gone.

    Deleted through the store because the route refuses this now; the state is
    still reachable from the catalog CLI and from a reload.
    """
    pid = load_catalog_item(store, tmp_path / "cat")
    assert asyncio.run(store.delete_project(pid))

    assert client.patch(f"/projects/{pid}", json={"name": "x"}).status_code == 404
    assert client.delete(f"/projects/{pid}").status_code == 404
