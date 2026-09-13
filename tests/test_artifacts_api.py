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


def _seed_with_unserveable_kind(store):
    """A stored artifact whose kind the serving layer has no media type for."""

    async def go():
        p = await store.create_project("P")
        v = await store.add_version(p.id, "x", "result=1", ok=True)
        d = Path(store.version_artifact_dir(v.id))
        (d / "sidecar.dat").write_bytes(b"whatever")
        await store.add_artifact(v.id, "sidecar", str(d / "sidecar.dat"))
        return v.id

    return asyncio.run(go())


def _seed_with_stl_parts(store, count):
    async def go():
        p = await store.create_project("P")
        v = await store.add_version(p.id, "x", "result=1", ok=True)
        d = Path(store.version_artifact_dir(v.id))
        for n in range(count):
            f = d / f"model_p{n}.stl"
            f.write_bytes(bytes([n]) * 84)
            await store.add_artifact(v.id, "stl", str(f))
        return v.id

    return asyncio.run(go())


def test_each_part_is_downloadable_by_its_number(client, store):
    """Every part is reachable, and they do not arrive under one filename.

    A browser saving three downloads that all claim to be the same file
    overwrites two of them, so the filename is part of what makes the parts
    reachable rather than a cosmetic detail.
    """
    vid = _seed_with_stl_parts(store, 3)
    seen = set()
    for n in range(3):
        r = client.get(f"/versions/{vid}/artifacts/stl/{n}")
        assert r.status_code == 200
        assert r.headers["content-type"] == "model/stl"
        assert r.content == bytes([n]) * 84
        seen.add(r.headers["content-disposition"])
    assert len(seen) == 3


def test_the_unnumbered_route_serves_the_first_part(client, store):
    """The route that existed before parts did keeps its answer and its filename.

    Every caller of it predates the idea of a part, so it has to stay usable
    without knowing about them.
    """
    vid = _seed_with_stl_parts(store, 3)
    r = client.get(f"/versions/{vid}/artifacts/stl")
    assert r.status_code == 200
    assert r.content == bytes([0]) * 84
    assert f"model_{vid}.stl" in r.headers["content-disposition"]


def test_a_kind_with_no_media_type_is_refused_rather_than_served(client, store):
    """A kind is user input on this route, unlike the fixed ones beside it.

    The row has to exist for this to mean anything: with no row, the not-found
    branch answers first and the media-type lookup is never reached, so the test
    would pass with or without the check it claims to guard. Nothing constrains
    a stored kind to one the browser is told how to render, so this is the case
    that turns a lookup into a server error.
    """
    vid = _seed_with_unserveable_kind(store)
    assert client.get(f"/versions/{vid}/artifacts/sidecar/0").status_code == 404


def test_a_part_beyond_the_last_is_not_found(client, store):
    vid = _seed_with_stl_parts(store, 2)
    assert client.get(f"/versions/{vid}/artifacts/stl/7").status_code == 404


def test_a_part_number_that_cannot_be_one_is_a_bad_request(client, store):
    """A number outside the ordinal's range is a malformed address, not a miss.

    The value is bound as a SQLite integer, and one too large to fit raises out
    of the driver instead of answering — which reaches the caller as a server
    error, saying the tool is broken rather than that the address is.
    """
    vid = _seed_with_stl_parts(store, 2)
    huge = "9" * 24
    assert client.get(f"/versions/{vid}/artifacts/stl/{huge}").status_code == 422
    assert client.get(f"/versions/{vid}/artifacts/stl/-1").status_code == 422


def test_the_wire_says_which_part_each_artifact_is(client, store):
    """Without this a client can see three files and name none of them."""
    vid = _seed_with_stl_parts(store, 3)
    r = client.get(f"/versions/{vid}")
    assert r.status_code == 200
    artifacts = r.json()["artifacts"]
    assert sorted(a["part"] for a in artifacts if a["kind"] == "stl") == [0, 1, 2]
