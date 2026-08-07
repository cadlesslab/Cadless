"""Artifact serving tests."""

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from cadless.store import Store


@pytest.fixture
def store(tmp_path):
    return Store(db_path=tmp_path / "db.sqlite", artifacts_dir=tmp_path / "artifacts")


@pytest.fixture
def client(store):
    with TestClient(create_app(store=store)) as c:
        yield c


def _seed_with_artifacts(store):
    async def go():
        p = await store.create_project("P")
        v = await store.add_version(p.id, "x", "result=1", ok=True)
        d = Path(store.version_artifact_dir(v.id))
        (d / "model.step").write_text("ISO-10303-21;")
        (d / "model.glb").write_bytes(b"glTF\x02\x00\x00\x00")
        (d / "model.stl").write_bytes(b"\x00" * 84)  # minimal binary STL stub
        (d / "model.obj").write_text("v 0 0 0\nf 1 1 1\n")
        for kind in ("step", "glb", "stl", "obj"):
            await store.add_artifact(v.id, kind, str(d / f"model.{kind}"))
        return v.id

    return asyncio.run(go())


def test_step_download(client, store):
    vid = _seed_with_artifacts(store)
    r = client.get(f"/versions/{vid}/artifacts/step")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/step"
    assert "attachment" in r.headers["content-disposition"]
    assert r.content == b"ISO-10303-21;"


def test_glb_fetch_inline(client, store):
    vid = _seed_with_artifacts(store)
    r = client.get(f"/versions/{vid}/artifacts/glb")
    assert r.status_code == 200
    assert r.headers["content-type"] == "model/gltf-binary"
    assert r.content.startswith(b"glTF")


def test_stl_download(client, store):
    vid = _seed_with_artifacts(store)
    r = client.get(f"/versions/{vid}/artifacts/stl")
    assert r.status_code == 200
    assert r.headers["content-type"] == "model/stl"
    assert "attachment" in r.headers["content-disposition"]
    assert f"model_{vid}.stl" in r.headers["content-disposition"]


def test_obj_download(client, store):
    vid = _seed_with_artifacts(store)
    r = client.get(f"/versions/{vid}/artifacts/obj")
    assert r.status_code == 200
    assert r.headers["content-type"] == "model/obj"
    assert "attachment" in r.headers["content-disposition"]
    assert r.text.startswith("v ")


def test_missing_artifact_404(client, store):
    async def seed():
        p = await store.create_project("P")
        v = await store.add_version(p.id, "x", "result=1", ok=True)
        return v.id

    vid = asyncio.run(seed())
    assert client.get(f"/versions/{vid}/artifacts/step").status_code == 404
    assert client.get("/versions/999/artifacts/glb").status_code == 404


def test_thumbnail_fetch_inline(client, store):
    async def seed():
        p = await store.create_project("P")
        v = await store.add_version(p.id, "x", "result=1", ok=True)
        d = Path(store.version_artifact_dir(v.id))
        (d / "thumbnail.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
        await store.add_artifact(v.id, "thumbnail", str(d / "thumbnail.png"))
        return v.id

    vid = asyncio.run(seed())
    r = client.get(f"/versions/{vid}/artifacts/thumbnail")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert "attachment" not in r.headers.get("content-disposition", "")
    assert r.content.startswith(b"\x89PNG")


def test_thumbnail_missing_404(client, store):
    async def seed():
        p = await store.create_project("P")
        v = await store.add_version(p.id, "x", "result=1", ok=True)
        return v.id

    vid = asyncio.run(seed())
    assert client.get(f"/versions/{vid}/artifacts/thumbnail").status_code == 404
