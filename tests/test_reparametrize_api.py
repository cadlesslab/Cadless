"""Parametric re-run endpoint tests.

Override-validation paths are pure (no geometry); the success path executes the
spliced script under build123d and is marked accordingly.
"""

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from cadless.store import Store

PARAMS_CODE = (
    "from build123d import *\n"
    'params = {"size": 10}\n'
    'result = Box(params["size"], params["size"], params["size"])\n'
)


@pytest.fixture
def store(tmp_path):
    return Store(db_path=tmp_path / "db.sqlite", artifacts_dir=tmp_path / "artifacts")


@pytest.fixture
def client(store):
    with TestClient(create_app(store=store)) as c:
        yield c


def _seed(store, code=PARAMS_CODE, params=None):
    params = {"size": 10} if params is None else params

    async def go():
        p = await store.create_project("P")
        v = await store.add_version(
            p.id, "a cube", code, ok=True, volume=1000.0, bbox=(10, 10, 10), parameters=params
        )
        return p.id, v.id

    return asyncio.run(go())


def test_reparametrize_unknown_param_400(client, store):
    _pid, vid = _seed(store)
    r = client.post(f"/versions/{vid}/reparametrize", json={"params": {"depth": 5}})
    assert r.status_code == 400
    assert "unknown parameter" in r.json()["detail"]


def test_reparametrize_version_without_params_400(client, store):
    _pid, vid = _seed(store, code="from build123d import *\nresult = Box(1,1,1)\n", params={})
    r = client.post(f"/versions/{vid}/reparametrize", json={"params": {"size": 5}})
    assert r.status_code == 400
    assert "no parameters" in r.json()["detail"]


def test_reparametrize_unknown_version_404(client):
    assert client.post("/versions/999/reparametrize", json={"params": {}}).status_code == 404


def test_reparametrize_rejects_catalog_item_403(client, store, tmp_path):
    """Catalog items are read-only — editing parameters must be refused (clone first)."""
    import json as _json

    from cadless.catalog.ledger import Ledger
    from cadless.catalog.loader import load_house

    house = tmp_path / "cat" / "deepcad-1"
    (house / "steps").mkdir(parents=True)
    (house / "steps" / "01.py").write_text(PARAMS_CODE)
    (house / "manifest.json").write_text(
        _json.dumps(
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

    vid = client.get(f"/projects/{pid}/versions").json()[-1]["id"]
    r = client.post(f"/versions/{vid}/reparametrize", json={"params": {"size": 20}})
    assert r.status_code == 403
    assert "read-only" in r.json()["detail"].lower()


@pytest.mark.build123d
def test_reparametrize_produces_new_geometry_without_llm(client, store):
    pid, vid = _seed(store)
    r = client.post(f"/versions/{vid}/reparametrize", json={"params": {"size": 20}})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True, data["error"]

    new = data["version"]
    assert new["id"] != vid  # a brand-new version
    assert new["parameters"] == {"size": 20}  # merged overrides persisted
    assert new["volume"] == pytest.approx(8000, rel=1e-3)  # 20^3, was 10^3=1000
    assert {a["kind"] for a in new["artifacts"]} == {"step", "glb", "stl", "obj"}

    # the reparametrized version becomes current
    assert client.get(f"/projects/{pid}").json()["current_version_id"] == new["id"]
    # the original version is untouched
    assert client.get(f"/versions/{vid}").json()["parameters"] == {"size": 10}
