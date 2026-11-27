"""A chat turn's lifetime when its client goes away.

These serve the app on a real server (``tests/live_server.py``), because a closed page
is a closed socket and ``TestClient`` never sends one. That is how a turn whose page
was closed came to be left ``pending`` for good while
``test_aborted_stream_leaves_no_dangling_pending`` passed: that test stops reading, and
its turn runs on to the end.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable, Iterator, Sequence

import httpx
import pytest
from fastapi.testclient import TestClient

import backend.routers.chat as chat
from backend.app import create_app
from cadless.llm.providers import StreamChunk
from cadless.llm.providers.fake import FakeChatProvider
from cadless.llm.types import StreamEvent
from cadless.store import Store
from tests.chat_api_helpers import (
    ScriptedProvider,
    StubPipeline,
    _install,
    _kb_entries,
    _messages,
    _text_turn,
    _tool_turn,
)
from tests.live_server import LiveServer, post_then_drop

# How long a turn has to finish once nothing holds it any more. It finishes in well
# under a second; the margin is for a loaded machine, not for the code under test.
SETTLE_S = 10.0


@pytest.fixture
def store(tmp_path):
    return Store(db_path=tmp_path / "db.sqlite", artifacts_dir=tmp_path / "artifacts")


class HeldProvider(FakeChatProvider):
    """A scripted provider whose first turn stops partway until ``release`` is set.

    It streams the first ``hold_at`` chunks of turn 0, sets ``reached``, and waits:
    the turn is then mid-model-call for as long as the test needs it to be.
    """

    def __init__(
        self,
        turns: Sequence[list[StreamChunk]],
        *,
        hold_at: int,
        reached: threading.Event,
        release: threading.Event,
    ) -> None:
        super().__init__()
        self._turns = list(turns)
        self._i = 0
        self._hold_at = hold_at
        self._reached = reached
        self._release = release

    def stream_turn(self, **kwargs) -> Iterator[StreamChunk]:
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})
        i = self._i
        self._i += 1
        turn = self._turns[min(i, len(self._turns) - 1)]
        if i != 0:
            yield from turn
            return
        yield from turn[: self._hold_at]
        self._reached.set()
        self._release.wait(timeout=SETTLE_S)
        yield from turn[self._hold_at :]


class HeldPipeline(StubPipeline):
    """A stub build that waits for ``release`` before it starts, having set ``reached``."""

    def __init__(self, *, reached: threading.Event, release: threading.Event) -> None:
        super().__init__()
        self._reached = reached
        self._release = release

    def run(self, *args, **kwargs):
        self._reached.set()
        self._release.wait(timeout=SETTLE_S)
        return super().run(*args, **kwargs)


def _response_finished(monkeypatch) -> threading.Event:
    """An event set once the chat route's response has ended, however it ended.

    A test waits on it before letting the turn go on, so the turn is still running
    when the server learns its client has gone — the order a real closed page has.
    """
    finished = threading.Event()

    class Noticing(chat.EventSourceResponse):
        async def __call__(self, scope, receive, send):
            try:
                await super().__call__(scope, receive, send)
            finally:
                finished.set()

    monkeypatch.setattr(chat, "EventSourceResponse", Noticing)
    return finished


def _wait_for(condition: Callable[[], bool]) -> bool:
    deadline = time.monotonic() + SETTLE_S
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.05)
    return condition()


def _staging_left(store: Store) -> list:
    staging = store.artifacts_dir / "_staging"
    return list(staging.iterdir()) if staging.exists() else []


def _current_version_id(store: Store, pid: int) -> int | None:
    project = asyncio.run(store.get_project(pid))
    return project.current_version_id if project else None


def _new_project(live: LiveServer) -> int:
    reply = httpx.post(f"http://127.0.0.1:{live.port}/projects", json={"name": "P"}, timeout=10)
    reply.raise_for_status()
    return reply.json()["id"]


def _assert_finished_with_its_reply(store: Store, pid: int, provider) -> None:
    """The turn ran to its end, with no reader, and kept everything a reader would see."""
    _wait_for(lambda: _messages(store, pid)[-1].status != "pending")
    user, assistant = _messages(store, pid)
    assert (user.role, assistant.role) == ("user", "assistant")
    assert assistant.status == "ok", f"the turn was left {assistant.status!r} by the closed page"
    assert assistant.version_id is not None
    assert assistant.version_id == _current_version_id(store, pid)
    assert any(b.kind == "text" and b.text == "Done." for b in assistant.blocks)
    assert len(provider.calls) == 2
    assert _wait_for(lambda: not _staging_left(store)), _staging_left(store)
    assert _wait_for(lambda: len(_kb_entries(store)) == 1), "the finished turn was not distilled"


def test_closing_the_page_during_the_model_call_lets_the_turn_finish(store, monkeypatch):
    reached, release = threading.Event(), threading.Event()
    tool = _tool_turn(tool_use_id="tu-1", name="generate_model", tool_input={"spec": "a cube"})
    text = _text_turn("Working on it.")
    # Turn 0 says something, so the page has an event to read, then holds before it
    # asks for the build.
    provider = HeldProvider(
        [[*text[:2], *tool[1:]], _text_turn("Done.")], hold_at=2, reached=reached, release=release
    )
    _install(monkeypatch, provider)
    finished = _response_finished(monkeypatch)

    with LiveServer(create_app(store=store)) as live:
        pid = _new_project(live)
        post_then_drop(
            live.port, f"/projects/{pid}/chat", {"message": "make a cube"}, drop_when=reached
        )
        assert finished.wait(SETTLE_S), "the server never noticed the page had gone"
        release.set()
        _assert_finished_with_its_reply(store, pid, provider)


def test_closing_the_page_while_events_pour_out_lets_the_turn_finish(store, monkeypatch):
    """The page goes while the server is busy writing to it, not waiting for the turn.

    A disconnect that lands in the middle of a send is cancelled at a different point
    than one that lands while the stream waits for the next event, and the turn must
    not care which.
    """
    tool = _tool_turn(tool_use_id="tu-1", name="generate_model", tool_input={"spec": "a cube"})
    flood = [StreamChunk(StreamEvent.TEXT_DELTA, {"text": "x" * 200}) for _ in range(3000)]
    provider = ScriptedProvider([[tool[0], *flood, *tool[1:]], _text_turn("Done.")])
    _install(monkeypatch, provider)
    finished = _response_finished(monkeypatch)

    with LiveServer(create_app(store=store)) as live:
        pid = _new_project(live)
        post_then_drop(live.port, f"/projects/{pid}/chat", {"message": "make a cube"})
        assert finished.wait(SETTLE_S), "the server never noticed the page had gone"
        _assert_finished_with_its_reply(store, pid, provider)


def test_a_turn_that_cannot_start_leaves_no_reply_pending(store, monkeypatch):
    """A failure between the reply row being written and the turn starting settles it."""
    _install(monkeypatch, ScriptedProvider([_text_turn("Done.")]))

    async def failing_compaction(history, provider):
        raise RuntimeError("the summary call failed")

    monkeypatch.setattr(chat, "compact_history", failing_compaction)

    with TestClient(create_app(store=store), raise_server_exceptions=False) as client:
        pid = client.post("/projects", json={"name": "P"}).json()["id"]
        assert client.post(f"/projects/{pid}/chat", json={"message": "x"}).status_code == 500

    user, assistant = _messages(store, pid)
    assert assistant.status == "error"
    assert assistant.error == "This turn could not start: the summary call failed"
    assert [b.text for b in assistant.blocks if b.kind == "text"] == [assistant.error]
    assert not _staging_left(store)


def test_closing_the_page_during_the_build_lets_the_turn_finish(store, monkeypatch):
    reached, release = threading.Event(), threading.Event()
    provider = ScriptedProvider(
        [
            _tool_turn(tool_use_id="tu-1", name="generate_model", tool_input={"spec": "a cube"}),
            _text_turn("Done."),
        ]
    )
    _install(monkeypatch, provider, HeldPipeline(reached=reached, release=release))
    finished = _response_finished(monkeypatch)

    with LiveServer(create_app(store=store)) as live:
        pid = _new_project(live)
        post_then_drop(
            live.port, f"/projects/{pid}/chat", {"message": "make a cube"}, drop_when=reached
        )
        assert finished.wait(SETTLE_S), "the server never noticed the page had gone"
        release.set()
        _assert_finished_with_its_reply(store, pid, provider)
