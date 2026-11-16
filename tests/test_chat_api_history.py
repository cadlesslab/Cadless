"""How a chat reply streams, and the lineage and replayed history a turn builds on."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

import backend.routers.chat as chat
from backend.app import create_app
from cadless.llm.providers import StreamChunk
from cadless.llm.types import StreamEvent
from cadless.store import Store
from tests.chat_api_helpers import (
    ScriptedProvider,
    StubPipeline,
    _install,
    _stream_chat,
    _text_turn,
    _tool_turn,
)


@pytest.fixture
def store(tmp_path):
    return Store(db_path=tmp_path / "db.sqlite", artifacts_dir=tmp_path / "artifacts")


@pytest.fixture
def client(store):
    with TestClient(create_app(store=store)) as c:
        yield c


# --- streaming hardening (/) ------------------------------


def _multi_text_turn(parts: list[str]) -> list[StreamChunk]:
    """A turn whose reply arrives as several TEXT_DELTA chunks (token streaming)."""
    chunks = [StreamChunk(StreamEvent.TURN_START)]
    chunks += [StreamChunk(StreamEvent.TEXT_DELTA, {"text": p}) for p in parts]
    chunks += [
        StreamChunk(StreamEvent.TURN_DELTA, {"stop_reason": "end_turn"}),
        StreamChunk(StreamEvent.USAGE, {"input_tokens": 10, "output_tokens": 5}),
        StreamChunk(StreamEvent.TURN_STOP),
    ]
    return chunks


def test_reply_streams_as_multiple_incremental_text_deltas(client, store, monkeypatch):
    """Each provider TEXT_DELTA is surfaced as its own SSE event (not coalesced)."""
    _install(monkeypatch, ScriptedProvider([_multi_text_turn(["Hel", "lo, ", "world"])]))
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    events = _stream_chat(client, pid, "hi")
    deltas = [e["text"] for e in events if e["event"] == "text_delta"]
    assert deltas == ["Hel", "lo, ", "world"]  # three separate frames, in order
    assert "".join(deltas) == "Hello, world"


def test_chat_sse_sets_anti_buffering_headers(client, monkeypatch):
    """The SSE response disables proxy buffering so deltas reach the client live."""
    _install(monkeypatch, ScriptedProvider([_text_turn("hi")]))
    pid = client.post("/projects", json={"name": "P"}).json()["id"]
    with client.stream("POST", f"/projects/{pid}/chat", json={"message": "hi"}) as r:
        assert r.status_code == 200
        assert r.headers.get("x-accel-buffering") == "no"
        assert "no-cache" in r.headers.get("cache-control", "")
        "".join(r.iter_text())  # drain so the turn settles cleanly


def test_generate_streams_codegen_delta_frames(client, store, monkeypatch):
    """A fresh generate_model surfaces the codegen tokens as live codegen_delta SSE
    frames, before that tool's tool_result (/3530)."""

    class CodegenPipeline(StubPipeline):
        def run(
            self,
            intent,
            export_dir=None,
            on_progress=None,
            prior_code=None,
            grounding=None,
            images=(),
            on_reading=None,
            assembly=None,
        ):
            if on_progress:  # emit codegen tokens the way the real pipeline now does
                on_progress({"event": "codegen", "text": "from build123d import *\n"})
                on_progress({"event": "codegen", "text": "result = Box(1, 1, 1)\n"})
            return super().run(
                intent,
                export_dir=export_dir,
                on_progress=on_progress,
                prior_code=prior_code,
                grounding=grounding,
            )

    provider = ScriptedProvider(
        [
            _tool_turn(tool_use_id="tu-1", name="generate_model", tool_input={"spec": "a cube"}),
            _text_turn("Done — built your cube."),
        ]
    )
    _install(monkeypatch, provider, CodegenPipeline())
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    events = _stream_chat(client, pid)
    codegen = [e["text"] for e in events if e["event"] == "codegen_delta"]
    assert codegen == ["from build123d import *\n", "result = Box(1, 1, 1)\n"]
    kinds = [e["event"] for e in events]
    # codegen streams live, before the tool settles, and never leaks into tool_progress
    assert kinds.index("codegen_delta") < kinds.index("tool_result")
    assert all(
        s.get("event") != "codegen"
        for e in events
        if e["event"] == "tool_progress"
        for s in [e["stage"]]
    )


# --- lineage + history-replay fixes -----------------------------------------


def test_chat_tool_version_chains_onto_current_version(client, store, monkeypatch):
    """A chat tool turn links its new version to the model the turn started on,
    instead of saving a disconnected root (lineage fix)."""
    provider = ScriptedProvider(
        [
            _tool_turn(tool_use_id="tu-1", name="edit_model", tool_input={"change": "wider"}),
            _text_turn("Done."),
        ]
    )
    _install(monkeypatch, provider)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    async def seed():
        v = await store.add_version(
            pid, "base", "result = 1\n", ok=True, volume=1.0, bbox=(1, 1, 1)
        )
        await store.set_current_version(pid, v.id)
        return v.id

    base_id = asyncio.run(seed())

    _stream_chat(client, pid, "make it wider")
    versions = client.get(f"/projects/{pid}/versions").json()
    new = [v for v in versions if v["id"] != base_id]
    assert new, "the edit turn persisted a new version"
    assert new[-1]["parent_version_id"] == base_id  # chained, not an orphan root


def test_a_turn_shows_the_orchestrator_the_measured_size_of_the_model_it_edits(
    client, store, monkeypatch
):
    provider = ScriptedProvider([_text_turn("Sure.")])
    _install(monkeypatch, provider)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    async def seed():
        v = await store.add_version(
            pid, "base", "result = 1\n", ok=True, volume=1.0, bbox=(84.0, 66.0, 36.05)
        )
        await store.set_current_version(pid, v.id)

    asyncio.run(seed())
    _stream_chat(client, pid, "make it 400 mm long, keeping its proportions")

    block = provider.calls[0]["messages"][-1].content[0].text
    assert "84 x 66 x 36.05 mm" in block


def test_a_damaged_catalog_ledger_leaves_a_project_nothing_was_copied_from_its_size(
    client, store, monkeypatch, tmp_path
):
    # Only a copy of a catalog item has a unit to look up there.
    provider = ScriptedProvider([_text_turn("Sure.")])
    _install(monkeypatch, provider)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    async def seed():
        v = await store.add_version(
            pid, "base", "result = 1\n", ok=True, volume=1.0, bbox=(84.0, 66.0, 36.05)
        )
        await store.set_current_version(pid, v.id)

    asyncio.run(seed())
    (tmp_path / "catalog-ledger.json").write_text("{not json")
    _stream_chat(client, pid, "make it 400 mm long, keeping its proportions")

    block = provider.calls[0]["messages"][-1].content[0].text
    assert "84 x 66 x 36.05 mm" in block


def test_replay_history_drops_invalid_assistant_blocks(store):
    """A flattened tool turn replays as plain text — no thinking/tool_use/tool_result
    blocks, which Bedrock rejects inside an assistant message (replay fix)."""
    from cadless.llm.types import ContentBlock

    async def go():
        await store.init()
        p = await store.create_project("P")
        sess = await store.get_or_create_session(p.id)
        await store.add_message(
            sess.id,
            "user",
            "make the roof wider",
            blocks=[ContentBlock.of_text("make the roof wider")],
        )
        await store.add_message(
            sess.id,
            "assistant",
            None,
            blocks=[
                ContentBlock.of_thinking("the user wants a wider roof"),
                ContentBlock.of_tool_use(id="tu-1", name="edit_model", input={"change": "wider"}),
                ContentBlock.of_tool_result(tool_use_id="tu-1", content="ok"),
                ContentBlock.of_text("Done! I've added a roof_width param."),
            ],
        )
        return await chat._replay_history(store, sess.id)

    msgs = asyncio.run(go())
    # Every replayed block is plain text (no thinking/tool_use/tool_result).
    assert {b.kind for m in msgs for b in m.content} == {"text"}
    assert [m.role for m in msgs] == ["user", "assistant"]  # valid alternation
    assert "Done! I've added a roof_width param." in msgs[1].content[0].text
    assert "the user wants" not in msgs[1].content[0].text  # past thinking dropped
