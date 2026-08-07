"""Messages read API tests: persisted transcript + legacy fallback."""

import asyncio

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


def test_real_messages_returned_in_seq_order(client, store):
    async def go():
        p = await store.create_project("P")
        v = await store.add_version(p.id, "make a cube", "code", ok=True)
        s = await store.get_or_create_session(p.id)
        await store.add_message(s.id, "user", "make a cube")
        a = await store.add_message(s.id, "assistant", None, status="pending")
        await store.update_message(a.id, status="ok", version_id=v.id)
        return p.id, v.id

    pid, vid = asyncio.run(go())
    msgs = client.get(f"/projects/{pid}/messages").json()
    assert [m["seq"] for m in msgs] == [1, 2]
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[0]["content"] == "make a cube"
    assert msgs[1]["status"] == "ok"
    assert msgs[1]["version_id"] == vid
    # MessageOut shape (now includes blocks)
    assert set(msgs[0]) == {
        "id",
        "seq",
        "role",
        "content",
        "status",
        "error",
        "version_id",
        "created_at",
        "blocks",
    }


def test_message_blocks_returned_in_payload(client, store):
    from cadless.llm.types import ContentBlock

    async def go():
        p = await store.create_project("P")
        s = await store.get_or_create_session(p.id)
        blocks = [
            ContentBlock.of_thinking("hmm", provider="bedrock", provider_raw={"signature": "xyz"}),
            ContentBlock.of_text("a cube", provider="bedrock"),
        ]
        await store.add_message(s.id, "assistant", "a cube", blocks=blocks)
        return p.id

    pid = asyncio.run(go())
    msgs = client.get(f"/projects/{pid}/messages").json()
    assert len(msgs) == 1
    blocks = msgs[0]["blocks"]
    assert [b["kind"] for b in blocks] == ["thinking", "text"]
    assert blocks[0]["provider"] == "bedrock"
    assert blocks[0]["provider_raw"] == {"signature": "xyz"}
    assert blocks[1]["text"] == "a cube"


def test_message_with_content_but_no_blocks_synthesizes_text_block(client, store):
    """A persisted message carrying plain ``content`` but no neutral blocks (e.g. a
    user turn from ``POST /chat``) surfaces a synthesized ``text`` block, so the
    frontend block-based transcript renders it. Mirrors the legacy fallback."""

    async def go():
        p = await store.create_project("P")
        s = await store.get_or_create_session(p.id)
        await store.add_message(s.id, "user", "hi")
        return p.id

    pid = asyncio.run(go())
    msgs = client.get(f"/projects/{pid}/messages").json()
    assert [b["kind"] for b in msgs[0]["blocks"]] == ["text"]
    assert msgs[0]["blocks"][0]["text"] == "hi"


def test_message_without_content_or_blocks_returns_empty_list(client, store):
    """A turn with neither content nor blocks (e.g. a pending/empty assistant row)
    yields no synthesized block."""

    async def go():
        p = await store.create_project("P")
        s = await store.get_or_create_session(p.id)
        await store.add_message(s.id, "assistant", None, status="pending")
        return p.id

    pid = asyncio.run(go())
    msgs = client.get(f"/projects/{pid}/messages").json()
    assert msgs[0]["blocks"] == []


def test_legacy_project_derives_transcript_from_versions(client, store):
    async def go():
        p = await store.create_project("Legacy")
        v1 = await store.add_version(p.id, "a cube", "code", ok=True)
        v2 = await store.add_version(p.id, "a sphere", None, ok=False, error="boom")
        return p.id, v1.id, v2.id

    pid, v1, v2 = asyncio.run(go())
    msgs = client.get(f"/projects/{pid}/messages").json()
    # one user + one assistant per version, ordered by version id
    assert [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant"]
    assert [m["seq"] for m in msgs] == [1, 2, 3, 4]
    assert msgs[0]["content"] == "a cube"
    assert msgs[1]["status"] == "ok"
    assert msgs[1]["version_id"] == v1
    assert msgs[1]["error"] is None
    assert msgs[2]["content"] == "a sphere"
    assert msgs[3]["status"] == "error"
    assert msgs[3]["error"] == "boom"
    assert msgs[3]["version_id"] == v2
    # legacy messages synthesize a single text block from content
    assert msgs[0]["blocks"] == [
        {
            "kind": "text",
            "text": "a cube",
            "id": None,
            "name": None,
            "input": None,
            "tool_use_id": None,
            "content": None,
            "is_error": False,
            "provider": None,
            "provider_raw": None,
        }
    ]
    # assistant message with no content -> no synthesized block
    assert msgs[1]["blocks"] == []


def test_unknown_project_404(client):
    assert client.get("/projects/999/messages").status_code == 404
