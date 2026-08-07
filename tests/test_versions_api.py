"""Version history API tests."""

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from cadless.store import Store
from tests.catalog_helpers import load_catalog_item


@pytest.fixture
def store(tmp_path):
    return Store(db_path=tmp_path / "db.sqlite", artifacts_dir=tmp_path / "artifacts")


@pytest.fixture
def client(store):
    with TestClient(create_app(store=store)) as c:
        yield c


def _seed(store, ok=True, code="from build123d import *\nresult = Box(5,5,5)"):
    async def go():
        p = await store.create_project("P")
        v = await store.add_version(p.id, "a cube", code, ok=ok, volume=125.0, bbox=(5, 5, 5))
        return p.id, v.id

    return asyncio.run(go())


def test_list_and_get_versions(client, store):
    pid, vid = _seed(store)
    lst = client.get(f"/projects/{pid}/versions").json()
    assert [v["id"] for v in lst] == [vid]
    got = client.get(f"/versions/{vid}").json()
    assert got["code"].startswith("from build123d") and got["volume"] == 125.0


def test_plan_step_in_version_payload(client, store):
    """plan_step is surfaced in the version payload for UI narration;
    a version with no plan step reports null."""

    async def go():
        p = await store.create_project("P")
        annotated = await store.add_version(
            p.id, "a cube", "result = Box(1,1,1)", ok=True, plan_step=3
        )
        plain = await store.add_version(p.id, "a rod", "result = Box(1,1,3)", ok=True)
        return p.id, annotated.id, plain.id

    pid, annotated_id, plain_id = asyncio.run(go())
    assert client.get(f"/versions/{annotated_id}").json()["plan_step"] == 3
    assert client.get(f"/versions/{plain_id}").json()["plan_step"] is None
    lst = {v["id"]: v for v in client.get(f"/projects/{pid}/versions").json()}
    assert lst[annotated_id]["plan_step"] == 3
    assert lst[plain_id]["plan_step"] is None


def test_list_versions_unknown_project_404(client):
    assert client.get("/projects/999/versions").status_code == 404


def test_get_version_404(client):
    assert client.get("/versions/999").status_code == 404


def test_list_candidates_returns_forge_losers(client, store):
    """GET /versions/{id}/candidates surfaces the forge best-of-N losers recorded
    against a winning version; a normal version reports an empty race."""

    async def go():
        p = await store.create_project("P")
        winner = await store.add_version(p.id, "a bracket", "result = Box(1,1,1)", ok=True)
        loser = await store.add_version(
            p.id, "a bracket", "result = Box(2,2,2)", ok=True, candidate_of_version_id=winner.id
        )
        plain = await store.add_version(p.id, "a rod", "result = Box(1,1,3)", ok=True)
        return winner.id, loser.id, plain.id

    winner_id, loser_id, plain_id = asyncio.run(go())

    cands = client.get(f"/versions/{winner_id}/candidates").json()
    assert [v["id"] for v in cands] == [loser_id]
    assert client.get(f"/versions/{plain_id}/candidates").json() == []


def test_list_candidates_unknown_version_404(client):
    assert client.get("/versions/999/candidates").status_code == 404


def test_set_current(client, store):
    pid, vid = _seed(store)
    r = client.post(f"/projects/{pid}/current", json={"version_id": vid})
    assert r.status_code == 200 and r.json()["current_version_id"] == vid
    assert client.get(f"/projects/{pid}").json()["current_version_id"] == vid


def test_set_current_bad_version_404(client, store):
    pid, _ = _seed(store)
    assert client.post(f"/projects/{pid}/current", json={"version_id": 999}).status_code == 404


def test_set_current_rejects_catalog_item_403(client, store, tmp_path):
    """Moving a catalog item's current version is a mutation like any other, and
    it is the one that strands the baked thumbnail on a version nobody points at."""
    pid = load_catalog_item(store, tmp_path / "cat")
    before = client.get(f"/projects/{pid}").json()["current_version_id"]
    first = client.get(f"/projects/{pid}/versions").json()[0]["id"]
    assert first != before  # the request would really move the pointer

    r = client.post(f"/projects/{pid}/current", json={"version_id": first})

    assert r.status_code == 403
    assert "read-only" in r.json()["detail"].lower()
    assert client.get(f"/projects/{pid}").json()["current_version_id"] == before


def test_set_current_on_a_deleted_catalog_project_is_404_not_403(client, store, tmp_path):
    """Deleting a project does not prune the ledger, so the entry outlives it.
    A project that is gone must read as gone rather than as read-only.

    Deleted through the store rather than through `DELETE /projects/{id}`, which
    refuses a catalog item now. The state it makes is still reachable — the
    catalog CLI clears projects this way, and so does a reload.
    """
    pid = load_catalog_item(store, tmp_path / "cat")
    vid = client.get(f"/projects/{pid}/versions").json()[0]["id"]
    assert asyncio.run(store.delete_project(pid))

    r = client.post(f"/projects/{pid}/current", json={"version_id": vid})

    assert r.status_code == 404


def test_rerun_rejects_catalog_item_403(client, store, tmp_path, monkeypatch):
    """Regression for #31: rerun re-executes step code and exports at scale 1.0,
    which would clobber baked catalog artifacts (houses bake at 1000x). Catalog
    items are read-only — rerun must refuse before touching the sandbox."""
    from cadless.catalog.ledger import Ledger
    from cadless.catalog.loader import load_house

    house = tmp_path / "cat" / "deepcad-1"
    (house / "steps").mkdir(parents=True)
    (house / "steps" / "01.py").write_text("from build123d import *\nresult = Box(5,5,5)\n")
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
    # The ledger lives beside the store db, where catalog_state.ledger_for looks.
    ledger = Ledger(Path(store.db_path).parent / "catalog-ledger.json")

    async def go():
        return await load_house(store, ledger, house)

    pid = asyncio.run(go())

    calls: list = []
    monkeypatch.setattr("backend.routers.versions.run_code", lambda *a, **k: calls.append((a, k)))

    vid = client.get(f"/projects/{pid}/versions").json()[-1]["id"]
    r = client.post(f"/versions/{vid}/rerun")
    assert r.status_code == 403
    assert "read-only" in r.json()["detail"].lower()
    assert calls == []  # the baked artifacts were never re-exported


@pytest.mark.build123d
def test_rerun_executes_stored_code_and_creates_artifacts(client, store):
    pid, vid = _seed(store)  # version has no artifacts yet
    r = client.post(f"/versions/{vid}/rerun")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True, data["error"]
    kinds = {a["kind"] for a in data["version"]["artifacts"]}
    assert kinds == {"step", "glb", "stl", "obj"}
