"""Generation endpoint tests. Pipeline is monkeypatched (no Bedrock)."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import backend.routers.generation as gen
from backend.app import create_app
from cadless.pipeline import GenerationResult
from cadless.store import Store
from tests.catalog_helpers import load_catalog_item


@pytest.fixture
def store(tmp_path):
    return Store(db_path=tmp_path / "db.sqlite", artifacts_dir=tmp_path / "artifacts")


@pytest.fixture
def client(store):
    with TestClient(create_app(store=store)) as c:
        yield c


def _fake_success(intent, export_dir, on_progress=None, prior_code=None):
    # write real artifact files into export_dir like the worker would
    (Path(export_dir) / "model.step").write_text("ISO-10303")
    (Path(export_dir) / "model.glb").write_bytes(b"glTF\x00\x00\x00")
    (Path(export_dir) / "model.stl").write_bytes(b"\x00" * 84)
    (Path(export_dir) / "model.obj").write_text("v 0 0 0\nf 1 1 1\n")
    return GenerationResult(
        ok=True,
        intent=intent,
        code="from build123d import *\nresult = Box(1,1,1)",
        volume=1.0,
        bbox=(1, 1, 1),
        step_path=str(Path(export_dir) / "model.step"),
        glb_path=str(Path(export_dir) / "model.glb"),
        stl_path=str(Path(export_dir) / "model.stl"),
        obj_path=str(Path(export_dir) / "model.obj"),
        attempts=[],
    )


def _fake_failure(intent, export_dir, on_progress=None, prior_code=None):
    return GenerationResult(ok=False, intent=intent, code="bad", error="boom", attempts=[])


def test_generate_success_persists_version_and_artifacts(client, store, monkeypatch):
    monkeypatch.setattr(gen, "generate_cad", _fake_success)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    r = client.post(f"/projects/{pid}/generate", json={"prompt": "a 1mm cube"})
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["version"]["volume"] == 1.0
    kinds = {a["kind"] for a in data["version"]["artifacts"]}
    assert kinds == {"step", "glb", "stl", "obj"}

    # current version set on the project
    assert client.get(f"/projects/{pid}").json()["current_version_id"] == data["version"]["id"]


def test_generate_rejects_catalog_item_403(client, store, tmp_path, monkeypatch):
    """Generating into a catalog project appends a version and moves its current
    pointer. The gate fires ahead of the pipeline, so no LLM call is needed to
    prove it — the recorder staying empty is the proof."""
    calls: list = []

    def _recording(*a, **k):
        calls.append((a, k))
        return _fake_success(*a, **k)

    monkeypatch.setattr(gen, "generate_cad", _recording)
    pid = load_catalog_item(store, tmp_path / "cat")
    before = client.get(f"/projects/{pid}").json()["current_version_id"]

    r = client.post(f"/projects/{pid}/generate", json={"prompt": "a 1mm cube"})

    assert r.status_code == 403
    assert "read-only" in r.json()["detail"].lower()
    assert calls == []
    assert client.get(f"/projects/{pid}").json()["current_version_id"] == before


def test_generate_stream_rejects_catalog_item_403(client, store, tmp_path, monkeypatch):
    """The SSE twin of the above: same gate, before the stream opens."""
    calls: list = []

    def _recording(*a, **k):
        calls.append((a, k))
        return _fake_success(*a, **k)

    monkeypatch.setattr(gen, "generate_cad", _recording)
    pid = load_catalog_item(store, tmp_path / "cat")
    before = client.get(f"/projects/{pid}").json()["current_version_id"]

    r = client.get(f"/projects/{pid}/generate/stream", params={"prompt": "a 1mm cube"})

    assert r.status_code == 403
    assert calls == []
    assert client.get(f"/projects/{pid}").json()["current_version_id"] == before


def test_generate_failure_persists_version_without_artifacts(client, monkeypatch):
    monkeypatch.setattr(gen, "generate_cad", _fake_failure)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    r = client.post(f"/projects/{pid}/generate", json={"prompt": "impossible"})
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is False
    assert data["version"]["error"] == "boom"
    assert data["version"]["artifacts"] == []
    # current version NOT set on failure
    assert client.get(f"/projects/{pid}").json()["current_version_id"] is None


def _messages_for_project(store, pid):
    import asyncio

    async def _go():
        sess = await store.get_or_create_session(pid)
        return sess, await store.list_messages(sess.id)

    return asyncio.run(_go())


def test_generate_success_persists_chat_turn(client, store, monkeypatch):
    monkeypatch.setattr(gen, "generate_cad", _fake_success)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    sess_before, _ = _messages_for_project(store, pid)
    before_updated = sess_before.updated_at

    data = client.post(f"/projects/{pid}/generate", json={"prompt": "a 1mm cube"}).json()

    sess_after, msgs = _messages_for_project(store, pid)
    # a user + assistant pair is created
    assert [m.role for m in msgs] == ["user", "assistant"]
    user, assistant = msgs
    assert user.content == "a 1mm cube"
    # the assistant settles to ok and links to the new version
    assert assistant.status == "ok"
    assert assistant.version_id == data["version"]["id"]
    # session updated_at advanced
    assert sess_after.updated_at >= before_updated
    assert sess_after.updated_at != ""


def test_generate_failure_persists_error_turn(client, store, monkeypatch):
    monkeypatch.setattr(gen, "generate_cad", _fake_failure)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    data = client.post(f"/projects/{pid}/generate", json={"prompt": "impossible"}).json()

    _, msgs = _messages_for_project(store, pid)
    assert [m.role for m in msgs] == ["user", "assistant"]
    assistant = msgs[1]
    # failed turn renders as an error message and still links to the failed version
    assert assistant.status == "error"
    assert assistant.error == "boom"
    assert assistant.version_id == data["version"]["id"]


def test_refine_user_message_uses_delta_prompt(client, store, monkeypatch):
    monkeypatch.setattr(gen, "generate_cad", _fake_success)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]
    parent = client.post(f"/projects/{pid}/generate", json={"prompt": "a cube"}).json()["version"]
    client.post(
        f"/projects/{pid}/generate",
        json={"prior_version_id": parent["id"], "delta_prompt": "make it 8mm"},
    )

    _, msgs = _messages_for_project(store, pid)
    # two runs -> two user+assistant pairs; the refine user message is the delta
    assert [m.role for m in msgs] == ["user", "assistant", "user", "assistant"]
    assert msgs[2].content == "make it 8mm"


def test_generate_unknown_project_404(client, monkeypatch):
    monkeypatch.setattr(gen, "generate_cad", _fake_success)
    assert client.post("/projects/999/generate", json={"prompt": "x"}).status_code == 404


def test_refine_passes_prior_code_and_records_lineage(client, store, monkeypatch):
    seen = {}

    def _fake_refine(intent, export_dir, on_progress=None, prior_code=None):
        seen["intent"] = intent
        seen["prior_code"] = prior_code
        return _fake_success(intent, export_dir, on_progress, prior_code)

    monkeypatch.setattr(gen, "generate_cad", _fake_success)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]
    parent = client.post(f"/projects/{pid}/generate", json={"prompt": "a cube"}).json()["version"]

    monkeypatch.setattr(gen, "generate_cad", _fake_refine)
    r = client.post(
        f"/projects/{pid}/generate",
        json={"prior_version_id": parent["id"], "delta_prompt": "make it 8mm"},
    )
    assert r.status_code == 200, r.text
    child = r.json()["version"]
    # the pipeline was driven in refine mode with the parent's code + the delta
    assert seen["intent"] == "make it 8mm"
    assert "Box(1,1,1)" in seen["prior_code"]
    # lineage + the delta-as-prompt are persisted on the new version
    assert child["parent_version_id"] == parent["id"]
    assert child["prompt"] == "make it 8mm"
    assert child["id"] != parent["id"]


def test_refine_without_delta_prompt_422(client, monkeypatch):
    monkeypatch.setattr(gen, "generate_cad", _fake_success)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]
    r = client.post(f"/projects/{pid}/generate", json={"prior_version_id": 1})
    assert r.status_code == 422


def test_refine_unknown_prior_version_404(client, monkeypatch):
    monkeypatch.setattr(gen, "generate_cad", _fake_success)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]
    r = client.post(
        f"/projects/{pid}/generate", json={"prior_version_id": 999, "delta_prompt": "tweak"}
    )
    assert r.status_code == 404


def test_generate_requires_prompt_or_refinement_422(client, monkeypatch):
    monkeypatch.setattr(gen, "generate_cad", _fake_success)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]
    assert client.post(f"/projects/{pid}/generate", json={}).status_code == 422
