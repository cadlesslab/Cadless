"""Artifact serving tests."""

import asyncio
import json
import sqlite3
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


def _seed_with_guide_frames(store, count):
    async def go():
        p = await store.create_project("P")
        v = await store.add_version(p.id, "x", "result=1", ok=True)
        d = Path(store.version_artifact_dir(v.id))
        for n in range(count):
            f = d / f"guide_f{n}.png"
            f.write_bytes(b"\x89PNG\r\n\x1a\n" + bytes([n]))
            await store.add_artifact(v.id, "guide", str(f))
        return v.id

    return asyncio.run(go())


def test_guide_frame_served_inline_as_png(client, store):
    """A guide frame is a picture, so it is displayed rather than downloaded --
    the same way a thumbnail is (``_INLINE``), and it reaches the browser only
    through the generic ``/{kind}/{part}`` route, since there is no fixed
    ``/guide`` route the way there is a ``/thumbnail`` one."""
    vid = _seed_with_guide_frames(store, 2)
    r = client.get(f"/versions/{vid}/artifacts/guide/1")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert "attachment" not in r.headers.get("content-disposition", "")
    assert r.content == b"\x89PNG\r\n\x1a\n" + bytes([1])


def _seed_with_motion(store, body: bytes | None):
    async def go():
        p = await store.create_project("P")
        v = await store.add_version(p.id, "x", "result=1", ok=True)
        if body is not None:
            f = Path(store.version_artifact_dir(v.id)) / "assembly.json"
            f.write_bytes(body)
            await store.add_artifact(v.id, "assembly", str(f))
        return v.id

    return asyncio.run(go())


def test_the_assembly_motion_is_served_inline_as_json(client, store):
    """The viewport reads it as data, so it is handed over in place as JSON
    rather than offered as a file to save, and on a fixed address of its own
    like the thumbnail's: there is one per version, not one per part."""
    body = b'{"order": [1, 0], "releases": [[0.0, 0.0, 1.0], []], "explode_fraction": 0.35}'
    vid = _seed_with_motion(store, body)
    r = client.get(f"/versions/{vid}/artifacts/assembly")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/json"
    assert "attachment" not in r.headers.get("content-disposition", "")
    assert r.content == body


def test_a_version_with_no_assembly_motion_is_not_found(client, store):
    # Most versions have none -- one part, or no assembly asked for -- and the
    # viewport takes a miss to mean there is nothing to play, not that the
    # server failed.
    vid = _seed_with_motion(store, None)
    r = client.get(f"/versions/{vid}/artifacts/assembly")
    assert r.status_code == 404
    # The route's own answer, which an address nothing serves would not give.
    assert r.json()["detail"] == "assembly artifact not found"


def test_the_wire_says_which_part_each_artifact_is(client, store):
    """Without this a client can see three files and name none of them."""
    vid = _seed_with_stl_parts(store, 3)
    r = client.get(f"/versions/{vid}")
    assert r.status_code == 200
    artifacts = r.json()["artifacts"]
    assert sorted(a["part"] for a in artifacts if a["kind"] == "stl") == [0, 1, 2]


def _seed_with_whole_assembly(store, parts=None):
    async def go():
        p = await store.create_project("P")
        v = await store.add_version(p.id, "x", "result=1", ok=True, parts=parts)
        d = Path(store.version_artifact_dir(v.id))
        for n in range(2):
            f = d / f"model_p{n}.stl"
            f.write_bytes(bytes([n]) * 84)
            await store.add_artifact(v.id, "stl", str(f))
        (d / "assembly.glb").write_bytes(b"glTF-whole")
        (d / "assembly.step").write_text("ISO-10303-21; whole")
        await store.add_artifact(v.id, "assembly_glb", str(d / "assembly.glb"))
        await store.add_artifact(v.id, "assembly_step", str(d / "assembly.step"))
        return v.id

    return asyncio.run(go())


def test_the_whole_assembly_glb_is_served_inline_on_its_own_address(client, store):
    vid = _seed_with_whole_assembly(store)

    r = client.get(f"/versions/{vid}/artifacts/assembly_glb")

    assert r.status_code == 200
    assert r.headers["content-type"] == "model/gltf-binary"
    assert "attachment" not in r.headers.get("content-disposition", "")
    assert r.content == b"glTF-whole"


def test_the_whole_assembly_step_is_offered_as_a_named_download(client, store):
    vid = _seed_with_whole_assembly(store)

    r = client.get(f"/versions/{vid}/artifacts/assembly_step")

    assert r.status_code == 200
    assert r.headers["content-type"] == "application/step"
    assert f"model_{vid}_assembly.step" in r.headers["content-disposition"]
    assert r.text == "ISO-10303-21; whole"


def test_a_version_with_no_whole_assembly_answers_not_found(client, store):
    vid = _seed_with_stl_parts(store, 1)

    r = client.get(f"/versions/{vid}/artifacts/assembly_glb")

    assert r.status_code == 404
    assert r.json() == {"detail": "assembly_glb artifact not found"}


def test_a_version_returns_the_names_its_build_gave_its_parts(client, store):
    parts = [
        {"index": 0, "name": "chassis", "path": ["car"], "color": None},
        {"index": 1, "name": "wheel", "path": ["car"], "color": [1.0, 0.0, 0.0, 1.0]},
    ]
    vid = _seed_with_whole_assembly(store, parts=parts)

    assert client.get(f"/versions/{vid}").json()["parts"] == parts


def test_a_version_stored_before_parts_had_names_names_them_by_number(client, store):
    vid = _seed_with_stl_parts(store, 3)

    assert client.get(f"/versions/{vid}").json()["parts"] == [
        {"index": n, "name": f"part{n}", "path": [], "color": None} for n in range(3)
    ]


def test_the_whole_assembly_files_are_not_served_by_part_number(client, store):
    vid = _seed_with_whole_assembly(store)

    r = client.get(f"/versions/{vid}/artifacts/assembly_step/0")

    assert r.status_code == 404
    assert r.json() == {"detail": "assembly_step has no numbered parts"}


def test_a_stored_list_that_does_not_number_the_files_gives_way_to_numbers(client, store):
    parts = [{"index": n, "name": f"named{n}", "path": [], "color": None} for n in range(3)]
    vid = _seed_with_whole_assembly(store, parts=parts)

    assert [p["name"] for p in client.get(f"/versions/{vid}").json()["parts"]] == [
        "part0",
        "part1",
    ]


def test_a_version_with_no_files_names_no_parts(client, store):
    async def go():
        p = await store.create_project("P")
        v = await store.add_version(
            p.id,
            "x",
            "result=1",
            ok=True,
            parts=[{"index": 0, "name": "a", "path": [], "color": None}],
        )
        return v.id

    vid = asyncio.run(go())

    assert client.get(f"/versions/{vid}").json()["parts"] == []


def test_a_project_s_versions_stay_readable_whatever_a_worker_reported(client, store):
    async def go():
        p = await store.create_project("P")
        v = await store.add_version(
            p.id, "x", "result=1", ok=True, parts=[{"index": 0, "name": 7, "path": []}]
        )
        f = Path(store.version_artifact_dir(v.id)) / "model.stl"
        f.write_bytes(b"\x00" * 84)
        await store.add_artifact(v.id, "stl", str(f))
        return p.id

    pid = asyncio.run(go())

    assert client.get(f"/projects/{pid}/versions").status_code == 200


def test_a_row_another_build_wrote_is_read_as_numbered_parts(client, store, tmp_path):
    """A row written past this build's checks -- by another engine build, or by
    hand -- is checked again where it is read."""
    vid = _seed_with_stl_parts(store, 1)
    with sqlite3.connect(tmp_path / "db.sqlite") as db:
        db.execute(
            "UPDATE script_versions SET parts_json=? WHERE id=?",
            (json.dumps([{"index": 0, "name": 7, "path": []}]), vid),
        )
    pid = client.get(f"/versions/{vid}").json()["project_id"]

    r = client.get(f"/projects/{pid}/versions")

    assert r.status_code == 200
    assert r.json()[0]["parts"] == [{"index": 0, "name": "part0", "path": [], "color": None}]
