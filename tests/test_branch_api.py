"""Branch-from-any-turn API tests.

`POST /projects/{id}/branch` forks a chosen prior version into a brand-new project
(the 1:1 project<->session model is how a separate line is represented): the new
project is seeded with a copy of the selected version's code/params as its starting
(and current) model, the branch origin is recorded, and the original project is
left completely untouched.
"""

import asyncio

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from cadless.store import Store

CODE = (
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


def _seed(store, *, params=None):
    """A project with two versions; the first (v1) is the branch source."""
    params = {"size": 10} if params is None else params

    async def go():
        p = await store.create_project("Origin")
        v1 = await store.add_version(
            p.id, "a cube", CODE, ok=True, volume=1000.0, bbox=(10, 10, 10), parameters=params
        )
        v2 = await store.add_version(
            p.id,
            "a bigger cube",
            CODE,
            ok=True,
            volume=8000.0,
            bbox=(20, 20, 20),
            parameters={"size": 20},
        )
        await store.set_current_version(p.id, v2.id)
        return p.id, v1.id, v2.id

    return asyncio.run(go())


def test_branch_creates_new_project_seeded_from_version(client, store):
    pid, v1, _v2 = _seed(store)
    r = client.post(f"/projects/{pid}/branch", json={"version_id": v1})
    assert r.status_code == 201, r.text
    body = r.json()

    new_pid = body["id"]
    assert new_pid != pid  # a brand-new project / line

    # The new line's starting model equals the selected version's code/params.
    versions = client.get(f"/projects/{new_pid}/versions").json()
    assert len(versions) == 1
    start = versions[0]
    assert start["code"] == CODE
    assert start["parameters"] == {"size": 10}
    # That seeded version is the new line's current model.
    assert body["current_version_id"] == start["id"]


def test_branch_records_origin(client, store):
    pid, v1, _v2 = _seed(store)
    body = client.post(f"/projects/{pid}/branch", json={"version_id": v1}).json()
    # The branch origin (source version) is recorded on the new project.
    assert body["branched_from_version_id"] == v1


def test_branch_leaves_original_unchanged(client, store):
    pid, v1, v2 = _seed(store)
    before = client.get(f"/projects/{pid}").json()
    before_versions = client.get(f"/projects/{pid}/versions").json()

    client.post(f"/projects/{pid}/branch", json={"version_id": v1})

    after = client.get(f"/projects/{pid}").json()
    after_versions = client.get(f"/projects/{pid}/versions").json()
    assert after["current_version_id"] == v2  # still the original current
    assert after == before
    assert after_versions == before_versions  # no new versions on the origin


def test_branch_unknown_project_404(client):
    assert client.post("/projects/999/branch", json={"version_id": 1}).status_code == 404


def test_branch_unknown_version_404(client, store):
    pid, _v1, _v2 = _seed(store)
    r = client.post(f"/projects/{pid}/branch", json={"version_id": 999})
    assert r.status_code == 404


def test_branch_version_from_other_project_404(client, store):
    pid, _v1, _v2 = _seed(store)
    other_pid, other_v1, _ = _seed(store)
    # A version that exists but belongs to a different project is not branchable here.
    r = client.post(f"/projects/{pid}/branch", json={"version_id": other_v1})
    assert r.status_code == 404
    assert other_pid != pid
