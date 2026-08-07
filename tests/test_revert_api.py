"""Explicit revert API + auto-revert-to-last-ok policy tests.

`POST /projects/{id}/revert` makes the implicit "current = last good" behavior
explicit and testable: revert to a given target version, or (target omitted) to
the project's LAST OK version. The chat turn-settlement path now GUARANTEES, on a
failed/aborted turn, that ``current_version_id`` points at the last OK version via
a named ``_last_ok_version_id`` helper — a successful turn keeps its new version.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.routers.chat import _last_ok_version_id
from cadless.store import Store
from tests.catalog_helpers import load_catalog_item

# Reuse the scripted-provider + stub-pipeline harness from the chat tests.
from tests.test_chat_api import (
    ScriptedProvider,
    StubPipeline,
    _install,
    _messages,
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


def _run(coro):
    return asyncio.run(coro)


async def _seed_version(store, pid, *, ok, prompt="v", set_current=False):
    v = await store.add_version(pid, prompt, "code" if ok else None, ok, None if ok else "boom")
    if set_current:
        await store.set_current_version(pid, v.id)
    return v


# --- explicit revert endpoint ----------------------------------------------


def test_revert_with_explicit_target_sets_current_to_it(client, store):
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    async def setup():
        v1 = await _seed_version(store, pid, ok=True, set_current=True)
        v2 = await _seed_version(store, pid, ok=True, set_current=True)
        return v1, v2

    v1, v2 = _run(setup())
    assert client.get(f"/projects/{pid}").json()["current_version_id"] == v2.id

    r = client.post(f"/projects/{pid}/revert", json={"version_id": v1.id})
    assert r.status_code == 200
    assert r.json()["id"] == v1.id
    assert client.get(f"/projects/{pid}").json()["current_version_id"] == v1.id


def test_revert_with_no_target_reverts_to_last_ok_version(client, store):
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    async def setup():
        await _seed_version(store, pid, ok=True, set_current=True)  # v1 ok
        v2 = await _seed_version(store, pid, ok=True, set_current=True)  # v2 ok (last ok)
        v3 = await _seed_version(store, pid, ok=False)  # v3 failed
        # Point current at the failed version to simulate a bad state.
        await store.set_current_version(pid, v3.id)
        return v2, v3

    v2_last_ok, v3_failed = _run(setup())
    assert client.get(f"/projects/{pid}").json()["current_version_id"] == v3_failed.id

    # No body / no target => revert to the last OK version (v2).
    r = client.post(f"/projects/{pid}/revert", json={})
    assert r.status_code == 200
    assert r.json()["id"] == v2_last_ok.id
    assert client.get(f"/projects/{pid}").json()["current_version_id"] == v2_last_ok.id


def test_revert_unknown_project_404(client):
    r = client.post("/projects/9999/revert", json={})
    assert r.status_code == 404


def test_revert_rejects_catalog_item_403(client, store, tmp_path):
    """Catalog items are read-only, but revert moved their current version
    freely — which is how a baked thumbnail ended up on a version the catalog
    URL no longer named. Same 403 as rerun/reparametrize."""
    pid = load_catalog_item(store, tmp_path / "cat")
    before = client.get(f"/projects/{pid}").json()["current_version_id"]
    first = client.get(f"/projects/{pid}/versions").json()[0]["id"]
    assert first != before  # the request would really move the pointer

    r = client.post(f"/projects/{pid}/revert", json={"version_id": first})

    assert r.status_code == 403
    assert "read-only" in r.json()["detail"].lower()
    assert client.get(f"/projects/{pid}").json()["current_version_id"] == before


def test_revert_to_nonexistent_version_400(client, store):
    pid = client.post("/projects", json={"name": "P"}).json()["id"]
    _run(_seed_version(store, pid, ok=True, set_current=True))
    r = client.post(f"/projects/{pid}/revert", json={"version_id": 999999})
    assert r.status_code == 400


def test_revert_to_wrong_project_version_400(client, store):
    pid_a = client.post("/projects", json={"name": "A"}).json()["id"]
    pid_b = client.post("/projects", json={"name": "B"}).json()["id"]

    async def setup():
        return await _seed_version(store, pid_b, ok=True, set_current=True)

    other = _run(setup())
    # Target belongs to project B but we revert project A.
    r = client.post(f"/projects/{pid_a}/revert", json={"version_id": other.id})
    assert r.status_code == 400


def test_revert_to_failed_version_400(client, store):
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    async def setup():
        await _seed_version(store, pid, ok=True, set_current=True)
        return await _seed_version(store, pid, ok=False)

    failed = _run(setup())
    r = client.post(f"/projects/{pid}/revert", json={"version_id": failed.id})
    assert r.status_code == 400


def test_revert_with_no_ok_version_400(client, store):
    """No target AND no OK version anywhere => nothing to revert to."""
    pid = client.post("/projects", json={"name": "P"}).json()["id"]
    _run(_seed_version(store, pid, ok=False))
    r = client.post(f"/projects/{pid}/revert", json={})
    assert r.status_code == 400


# --- _last_ok_version_id helper --------------------------------------------


def test_last_ok_version_id_returns_most_recent_ok(store):
    async def go():
        await store.init()
        p = await store.create_project("P")
        await store.add_version(p.id, "v1", "c", True)
        v2 = await store.add_version(p.id, "v2", "c", True)
        await store.add_version(p.id, "v3", None, False, "boom")
        return p.id, v2.id

    pid, last_ok = _run(go())
    assert _run(_last_ok_version_id(store, pid)) == last_ok


def test_last_ok_version_id_none_when_no_ok_version(store):
    async def go():
        await store.init()
        p = await store.create_project("P")
        await store.add_version(p.id, "v1", None, False, "boom")
        return p.id

    pid = _run(go())
    assert _run(_last_ok_version_id(store, pid)) is None


# --- auto-revert-to-last-ok in turn settlement ------------------------------


def test_failed_turn_leaves_current_at_last_ok_version(client, store, monkeypatch):
    """A FAILED turn must leave current pointing at the last OK version, not the
    failed/partial one."""
    provider = ScriptedProvider(
        [
            _tool_turn(tool_use_id="tu-1", name="generate_model", tool_input={"spec": "x"}),
            _text_turn("Sorry, that failed."),
        ]
    )
    _install(monkeypatch, provider, pipeline=StubPipeline(ok=False, error="boom"))
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    # Seed a prior known-good current version.
    async def setup():
        v = await store.add_version(pid, "good", "code", True)
        await store.set_current_version(pid, v.id)
        return v.id

    good_id = _run(setup())

    _stream_chat(client, pid, "break it")

    # The turn failed; current is guaranteed to still be the last OK version.
    assert client.get(f"/projects/{pid}").json()["current_version_id"] == good_id
    msgs = _messages(store, pid)
    assert msgs[-1].status == "error"


def test_aborted_turn_leaves_current_at_last_ok_version(client, store, monkeypatch):
    """An ABORTED / provider-exception turn likewise leaves current at last OK."""
    from cadless.llm.providers.fake import FakeChatProvider

    class BoomProvider(FakeChatProvider):
        def stream_turn(self, **kwargs):
            raise RuntimeError("kaboom")
            yield  # pragma: no cover

    _install(monkeypatch, BoomProvider())
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    async def setup():
        v = await store.add_version(pid, "good", "code", True)
        await store.set_current_version(pid, v.id)
        return v.id

    good_id = _run(setup())

    _stream_chat(client, pid, "explode")

    assert client.get(f"/projects/{pid}").json()["current_version_id"] == good_id
    msgs = _messages(store, pid)
    assert msgs[-1].status == "error"


def test_successful_turn_keeps_its_new_version(client, store, monkeypatch):
    """A successful turn's behavior is unchanged: current is its NEW version, not a
    prior one."""
    provider = ScriptedProvider(
        [
            _tool_turn(tool_use_id="tu-1", name="generate_model", tool_input={"spec": "a cube"}),
            _text_turn("Done."),
        ]
    )
    _install(monkeypatch, provider)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    async def setup():
        v = await store.add_version(pid, "old", "code", True)
        await store.set_current_version(pid, v.id)
        return v.id

    old_id = _run(setup())

    events = _stream_chat(client, pid, "make a cube")
    tresult = next(e for e in events if e["event"] == "tool_result")
    new_id = tresult["version_id"]

    assert new_id != old_id
    assert client.get(f"/projects/{pid}").json()["current_version_id"] == new_id
