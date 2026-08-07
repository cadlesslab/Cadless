"""Pipeline progress hook + SSE streaming tests."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import backend.routers.generation as gen
from backend.app import create_app
from cadless.config import Settings
from cadless.pipeline import STAGE_PHASES, GenerationResult, Pipeline
from cadless.store import Store

BANNED = "import os\nfrom build123d import *\nresult = Box(1,1,1)\n"
GOOD_PARAMS = (
    "from build123d import *\n"
    'params = {"size": 10}\n'
    'result = Box(params["size"], params["size"], params["size"])\n'
)


class FakeGen:
    def __init__(self, outputs):
        self.outputs = outputs
        self.repairs = 0

    def generate(self, intent, grounding=None, temperature=None, on_token=None):
        out = self.outputs[0]
        if on_token is not None:  # surface the codegen stream
            on_token(out)
        return out

    def refine(self, intent, prior_code):
        return self.outputs[0]

    def repair(self, intent, code, error, context=None):
        self.repairs += 1
        return self.outputs[self.repairs]


def test_pipeline_emits_start_and_attempt_events():
    events = []
    gen_ = FakeGen([BANNED, BANNED])  # validation fails twice -> no execution (no build123d)
    Pipeline(generator=gen_, config=Settings(repair_max_attempts=2)).run(
        "x", on_progress=events.append
    )
    kinds = [e["event"] for e in events]
    assert kinds[0] == "start"
    attempts = [e for e in events if e["event"] == "attempt"]
    assert len(attempts) == 2
    assert all(a["stage"] == "validate" and a["ok"] is False for a in attempts)


@pytest.fixture
def store(tmp_path):
    return Store(db_path=tmp_path / "db.sqlite", artifacts_dir=tmp_path / "artifacts")


@pytest.fixture
def client(store):
    with TestClient(create_app(store=store)) as c:
        yield c


def _fake_generate(intent, export_dir, on_progress=None, prior_code=None):
    if on_progress:
        on_progress({"event": "start", "intent": intent, "max_tries": 3})
        on_progress({"event": "stage", "phase": "build", "status": "ok", "attempt": 1})
        on_progress({"event": "attempt", "n": 1, "stage": "execute", "ok": True, "error": None})
    (Path(export_dir) / "model.step").write_text("ISO")
    (Path(export_dir) / "model.glb").write_bytes(b"glTF\x00")
    return GenerationResult(
        ok=True,
        intent=intent,
        code="result = Box(1,1,1)",
        volume=1.0,
        bbox=(1, 1, 1),
        step_path=str(Path(export_dir) / "model.step"),
        glb_path=str(Path(export_dir) / "model.glb"),
        attempts=[],
    )


def test_sse_stream_emits_progress_then_done(client, store, monkeypatch):
    monkeypatch.setattr(gen, "generate_cad", _fake_generate)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    with client.stream("GET", f"/projects/{pid}/generate/stream", params={"prompt": "a cube"}) as r:
        assert r.status_code == 200
        body = "".join(r.iter_text())

    assert '"event": "start"' in body
    assert '"event": "stage"' in body  # granular events pass through the stream
    assert '"event": "attempt"' in body
    assert '"event": "done"' in body
    assert '"version_id"' in body
    # the version was persisted
    versions = client.get(f"/projects/{pid}/versions").json()
    assert len(versions) == 1 and versions[0]["ok"] is True


def _messages_for_project(store, pid):
    import asyncio

    async def _go():
        sess = await store.get_or_create_session(pid)
        return await store.list_messages(sess.id)

    return asyncio.run(_go())


def test_sse_stream_persists_chat_turn(client, store, monkeypatch):
    monkeypatch.setattr(gen, "generate_cad", _fake_generate)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    with client.stream("GET", f"/projects/{pid}/generate/stream", params={"prompt": "a cube"}) as r:
        "".join(r.iter_text())

    msgs = _messages_for_project(store, pid)
    assert [m.role for m in msgs] == ["user", "assistant"]
    assert msgs[0].content == "a cube"
    assert msgs[1].status == "ok" and msgs[1].version_id is not None


def test_sse_stream_marks_turn_error_on_unexpected_exception(client, store, monkeypatch):
    def _boom(intent, export_dir, on_progress=None, prior_code=None):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(gen, "generate_cad", _boom)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    with client.stream("GET", f"/projects/{pid}/generate/stream", params={"prompt": "a cube"}) as r:
        body = "".join(r.iter_text())

    assert '"event": "error"' in body
    msgs = _messages_for_project(store, pid)
    # the dangling pending assistant turn is settled to error so a reload shows it
    assert [m.role for m in msgs] == ["user", "assistant"]
    assert msgs[1].status == "error"
    assert "kaboom" in (msgs[1].error or "")


def test_sse_unknown_project_404(client):
    assert client.get("/projects/999/generate/stream", params={"prompt": "x"}).status_code == 404


# ---- staged lifecycle ------------------------------------------


def _stages(events):
    return [(e["phase"], e["status"]) for e in events if e["event"] == "stage"]


@pytest.mark.build123d
def test_pipeline_emits_full_staged_lifecycle_on_success(tmp_path):
    events = []
    gen_ = FakeGen([GOOD_PARAMS])
    Pipeline(generator=gen_).run("a cube", export_dir=str(tmp_path), on_progress=events.append)

    # legacy events are still present and ordered (backward compatible)
    assert events[0]["event"] == "start" and events[0]["mode"] == "generate"
    assert any(e["event"] == "attempt" and e["stage"] == "execute" and e["ok"] for e in events)

    stages = _stages(events)
    for expected in [
        ("interpret", "ok"),
        ("generate", "begin"),
        ("generate", "ok"),
        ("validate", "ok"),
        ("build", "ok"),
        ("mesh", "ok"),
    ]:
        assert expected in stages, f"missing stage {expected} in {stages}"
    # every emitted phase is part of the documented vocabulary
    assert {phase for phase, _ in stages} <= set(STAGE_PHASES)


def test_pipeline_emits_repair_stage_between_failed_attempts():
    events = []
    gen_ = FakeGen([BANNED, BANNED])  # validation fails twice -> no execution (no build123d)
    Pipeline(generator=gen_, config=Settings(repair_max_attempts=2)).run(
        "x", on_progress=events.append
    )
    stages = _stages(events)
    assert ("validate", "error") in stages
    assert ("repair", "begin") in stages and ("repair", "ok") in stages
    # the build stage is never reached because validation never passes
    assert not any(phase == "build" for phase, _ in stages)


def test_refine_mode_reported_in_start_and_generate_stage():
    events = []
    gen_ = FakeGen([BANNED])  # fails validation immediately; no execution
    Pipeline(generator=gen_, config=Settings(repair_max_attempts=1)).run(
        "make it bigger",
        prior_code="from build123d import *\nresult = Box(5,5,5)",
        on_progress=events.append,
    )
    assert events[0]["mode"] == "refine"
    assert ("refine", "begin") in _stages(events) and ("refine", "ok") in _stages(events)
