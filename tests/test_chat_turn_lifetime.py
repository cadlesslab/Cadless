"""A chat turn's lifetime: when its client goes away, is stopped, or outlives its server.

The disconnect cases serve the app on a real server (``tests/live_server.py``), because
a closed page is a closed socket and ``TestClient`` never sends one: a test there that
stops reading leaves its turn to run on to the end, and passes whether or not a closed
page would have ended the turn.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

import backend.app as app_module
import backend.routers.chat as chat
from backend.app import create_app
from backend.turns import INTERRUPTED_NOTE, TurnRegistry
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
    _stream_chat,
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


async def _until(go: threading.Event) -> None:
    """Hold a coroutine of the app's until the test says go.

    Polled on the loop rather than waited on in a thread, so a cancel reaches it and
    a server shutting down is not left waiting on a thread it cannot stop.
    """
    deadline = time.monotonic() + SETTLE_S
    while not go.is_set() and time.monotonic() < deadline:
        await asyncio.sleep(0.01)


def _draining(monkeypatch) -> threading.Event:
    """An event set once a shutdown has begun draining the running turns."""
    began = threading.Event()
    drain = TurnRegistry.drain

    async def noticing(self, grace_s: float) -> None:
        began.set()
        await drain(self, grace_s)

    monkeypatch.setattr(TurnRegistry, "drain", noticing)
    return began


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


async def _failing_compaction(history, provider):
    raise RuntimeError("the summary call failed reading /srv/private/notes")


def _failing_agent(**kwargs):
    raise RuntimeError("the agent could not be built from /srv/private/keys")


@pytest.mark.parametrize("site", ["compact_history", "Agent"])
def test_a_turn_that_cannot_start_leaves_nothing_behind(store, monkeypatch, site):
    """A failure anywhere from the reply row being written to the turn starting.

    The reply settles, saying only that the turn could not start, since the
    failure's own text would be served to whoever reads the transcript. The staging
    directory goes, and so does the turn, which nothing would ever launch.
    """
    _install(monkeypatch, ScriptedProvider([_text_turn("Done.")]))
    failing = {"compact_history": _failing_compaction, "Agent": _failing_agent}[site]
    monkeypatch.setattr(chat, site, failing)
    app = create_app(store=store)

    with TestClient(app, raise_server_exceptions=False) as client:
        pid = client.post("/projects", json={"name": "P"}).json()["id"]
        assert client.post(f"/projects/{pid}/chat", json={"message": "x"}).status_code == 500

    user, assistant = _messages(store, pid)
    assert assistant.status == "error"
    assert assistant.error == "This turn could not start."
    assert [b.text for b in assistant.blocks if b.kind == "text"] == ["This turn could not start."]
    assert not _staging_left(store)
    assert app.state.turns.for_session(user.session_id) == []


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


# --- Stop ---------------------------------------------------------------------


def _stop_when(client, pid: int, reached: threading.Event, release: threading.Event) -> dict:
    """Post a stop once ``reached`` is set, then let the turn go on.

    The answer lands in the returned dict, for the test to read once the stream it
    runs meanwhile has ended.
    """
    answers: dict = {}

    def stop() -> None:
        reached.wait(SETTLE_S)
        answers["stop"] = client.post(f"/projects/{pid}/chat/stop", json={})
        release.set()

    threading.Thread(target=stop, daemon=True).start()
    return answers


def test_stop_during_a_build_ends_the_turn_and_keeps_the_build(store, monkeypatch):
    reached, release = threading.Event(), threading.Event()
    provider = ScriptedProvider(
        [
            _tool_turn(tool_use_id="tu-1", name="generate_model", tool_input={"spec": "a cube"}),
            _text_turn("never sent"),
        ]
    )
    _install(monkeypatch, provider, HeldPipeline(reached=reached, release=release))

    with TestClient(create_app(store=store)) as client:
        pid = client.post("/projects", json={"name": "P"}).json()["id"]
        answers = _stop_when(client, pid, reached, release)
        events = _stream_chat(client, pid)

    assert answers["stop"].status_code == 202
    assert answers["stop"].json() == {"stopping": True}
    # The build that was running finished; no model call followed it.
    assert len(provider.calls) == 1
    assert [e["stop_reason"] for e in events if e["event"] == "turn_end"] == ["stopped"]
    _, assistant = _messages(store, pid)
    assert assistant.status == "stopped"
    assert assistant.version_id is not None
    assert assistant.version_id == _current_version_id(store, pid)
    assert assistant.blocks[-1].text == "\n\nStopped. Kept the last build."
    assert not _staging_left(store)
    assert _kb_entries(store) == [], "a stopped turn was distilled"


def test_stop_during_the_first_model_call_builds_nothing(store, monkeypatch):
    reached, release = threading.Event(), threading.Event()
    tool = _tool_turn(tool_use_id="tu-1", name="generate_model", tool_input={"spec": "a cube"})
    provider = HeldProvider(
        [tool, _text_turn("never sent")], hold_at=1, reached=reached, release=release
    )
    pipeline = StubPipeline()
    _install(monkeypatch, provider, pipeline)

    with TestClient(create_app(store=store)) as client:
        pid = client.post("/projects", json={"name": "P"}).json()["id"]
        answers = _stop_when(client, pid, reached, release)
        events = _stream_chat(client, pid)

    assert answers["stop"].json() == {"stopping": True}
    assert pipeline.groundings == [], "the build the model asked for ran after the stop"
    assert not [e for e in events if e["event"] == "tool_start"]
    _, assistant = _messages(store, pid)
    assert assistant.status == "stopped"
    assert assistant.version_id is None
    assert assistant.blocks[-1].text == "\n\nStopped before anything was built."


def test_stop_with_no_turn_left_to_stop_says_so(store, monkeypatch):
    _install(monkeypatch, ScriptedProvider([_text_turn("Done.")]))

    with TestClient(create_app(store=store)) as client:
        pid = client.post("/projects", json={"name": "P"}).json()["id"]
        before = client.post(f"/projects/{pid}/chat/stop", json={})
        _stream_chat(client, pid)
        after = client.post(f"/projects/{pid}/chat/stop", json={})
        unknown = client.post("/projects/999999/chat/stop", json={})

    assert (before.status_code, before.json()) == (202, {"stopping": False})
    # The stop before the turn reached nothing, and nothing kept it for the turn:
    # that one ran to its end. The one after reached nothing either.
    assert _messages(store, pid)[-1].status == "ok"
    assert after.json() == {"stopping": False}
    assert unknown.status_code == 404


def test_a_stop_must_be_sent_as_json(store, monkeypatch):
    """A page on another site can send an empty or a plain-text POST without the
    browser asking this server first, and a stop sent that way would end someone's
    turn."""
    _install(monkeypatch, ScriptedProvider([_text_turn("Done.")]))

    with TestClient(create_app(store=store)) as client:
        pid = client.post("/projects", json={"name": "P"}).json()["id"]
        bare = client.post(f"/projects/{pid}/chat/stop")
        plain = client.post(
            f"/projects/{pid}/chat/stop", content="{}", headers={"content-type": "text/plain"}
        )
        as_json = client.post(f"/projects/{pid}/chat/stop", json={})

    assert (bare.status_code, plain.status_code, as_json.status_code) == (422, 422, 202)


def test_a_stop_during_the_turns_lead_up_reaches_it(store, monkeypatch):
    """The lead-up to a turn can take a model call of its own, folding a long
    transcript, and a stop asked for meanwhile ends the turn before its first."""
    compacting, release = threading.Event(), threading.Event()

    async def held_compaction(history, provider):
        compacting.set()
        await _until(release)
        return history

    tool = _tool_turn(tool_use_id="tu-1", name="generate_model", tool_input={"spec": "a cube"})
    provider = ScriptedProvider([tool, _text_turn("never sent")])
    _install(monkeypatch, provider)
    monkeypatch.setattr(chat, "compact_history", held_compaction)

    with TestClient(create_app(store=store)) as client:
        pid = client.post("/projects", json={"name": "P"}).json()["id"]
        answers = _stop_when(client, pid, compacting, release)
        _stream_chat(client, pid)

    assert answers["stop"].json() == {"stopping": True}
    assert provider.calls == []
    _, assistant = _messages(store, pid)
    assert assistant.status == "stopped"
    assert assistant.blocks[-1].text == "\n\nStopped before anything was built."


def test_a_stop_after_the_reply_has_settled_answers_no(store, monkeypatch):
    """The turn's task runs on past its settle, learning from what it built. A stop
    then can change nothing, and says so rather than claiming to stop it."""
    distilling, release = threading.Event(), threading.Event()

    async def held_distill(*args, **kwargs):
        distilling.set()
        await _until(release)

    tool = _tool_turn(tool_use_id="tu-1", name="generate_model", tool_input={"spec": "a cube"})
    _install(monkeypatch, ScriptedProvider([tool, _text_turn("Done.")]))
    monkeypatch.setattr(chat, "auto_distill", held_distill)

    with TestClient(create_app(store=store)) as client:
        pid = client.post("/projects", json={"name": "P"}).json()["id"]
        tab = threading.Thread(target=_stream_chat, args=(client, pid), daemon=True)
        tab.start()
        try:
            assert distilling.wait(SETTLE_S), "the turn never settled"
            answer = client.post(f"/projects/{pid}/chat/stop", json={})
        finally:
            release.set()
        tab.join(SETTLE_S)

    assert answer.json() == {"stopping": False}
    assert _messages(store, pid)[-1].status == "ok"


def test_a_stop_reaches_every_turn_running_in_the_session(store, monkeypatch):
    """Two tabs can each start a turn on one project, and Stop ends both."""
    entered = threading.Semaphore(0)
    release = threading.Event()

    class HeldTwice(StubPipeline):
        def run(self, *args, **kwargs):
            entered.release()
            release.wait(SETTLE_S)
            return super().run(*args, **kwargs)

    tool = _tool_turn(tool_use_id="tu-1", name="generate_model", tool_input={"spec": "a cube"})
    provider = ScriptedProvider([tool])
    _install(monkeypatch, provider, HeldTwice())

    with TestClient(create_app(store=store)) as client:
        pid = client.post("/projects", json={"name": "P"}).json()["id"]
        tabs = [
            threading.Thread(target=_stream_chat, args=(client, pid), daemon=True) for _ in range(2)
        ]
        for tab in tabs:
            tab.start()
        assert entered.acquire(timeout=SETTLE_S)
        assert entered.acquire(timeout=SETTLE_S)
        answer = client.post(f"/projects/{pid}/chat/stop", json={})
        release.set()
        for tab in tabs:
            tab.join(SETTLE_S)

    assert answer.json() == {"stopping": True}
    replies = [m for m in _messages(store, pid) if m.role == "assistant"]
    assert [m.status for m in replies] == ["stopped", "stopped"]
    assert len(provider.calls) == 2


def test_a_stopped_turn_still_settles_when_its_page_closes(store, monkeypatch):
    reached, release = threading.Event(), threading.Event()
    provider = ScriptedProvider(
        [
            _tool_turn(tool_use_id="tu-1", name="generate_model", tool_input={"spec": "a cube"}),
            _text_turn("never sent"),
        ]
    )
    _install(monkeypatch, provider, HeldPipeline(reached=reached, release=release))
    finished = _response_finished(monkeypatch)

    with LiveServer(create_app(store=store)) as live:
        pid = _new_project(live)
        post_then_drop(
            live.port, f"/projects/{pid}/chat", {"message": "make a cube"}, drop_when=reached
        )
        assert finished.wait(SETTLE_S)
        stop = httpx.post(
            f"http://127.0.0.1:{live.port}/projects/{pid}/chat/stop", json={}, timeout=10
        )
        release.set()
        assert stop.json() == {"stopping": True}
        assert _wait_for(lambda: _messages(store, pid)[-1].status != "pending")

    assert _messages(store, pid)[-1].status == "stopped"
    assert len(provider.calls) == 1


# --- a server that stops, or stopped, while a turn ran --------------------------


def _left_pending(store: Store, *, built: bool) -> tuple[int, int | None]:
    """A project whose reply an earlier process left ``pending``, as a crash leaves it.

    Built, it has two versions and its user has since gone back to the first, as
    happens to a reply stuck for days. Returns the project and its current version.
    """

    async def seed():
        await store.init()
        project = await store.create_project("P")
        session = await store.get_or_create_session(project.id)
        await store.add_message(session.id, "user", "make a cube")
        await store.add_message(session.id, "assistant", None, status="pending")
        if not built:
            return project.id, None
        first = await store.add_version(project.id, "make a cube", "result = None", True)
        await store.add_version(project.id, "make it taller", "result = None", True)
        await store.set_current_version(project.id, first.id)
        return project.id, first.id

    return asyncio.run(seed())


def test_a_start_settles_the_replies_an_earlier_process_left_pending(store):
    pid, current = _left_pending(store, built=True)

    with TestClient(create_app(store=store)):
        pass

    _, reply = _messages(store, pid)
    assert reply.status == "interrupted"
    assert reply.error == INTERRUPTED_NOTE
    assert reply.blocks[-1].text == INTERRUPTED_NOTE
    # Nothing says which version was the dead turn's, so the reply links none, and
    # the project stays on the version its user chose.
    assert reply.version_id is None
    assert _current_version_id(store, pid) == current


def test_the_sweep_settles_only_replies_older_than_its_cutoff(store):
    """Only a reply older than the process could have nothing running in it."""
    pid, _ = _left_pending(store, built=False)
    reply = _messages(store, pid)[-1]
    just_after = (datetime.fromisoformat(reply.created_at) + timedelta(microseconds=1)).isoformat()

    assert asyncio.run(store.settle_interrupted_turns(before=reply.created_at, note="x")) == []
    assert _messages(store, pid)[-1].status == "pending"
    assert asyncio.run(store.settle_interrupted_turns(before=just_after, note="x")) == [reply.id]


def test_a_reply_the_sweep_cannot_read_does_not_hold_back_the_rest(store):
    """A reply whose stored blocks are unreadable settles as it is, and the replies
    after it settle too. A sweep that stopped at it would stop there on every start."""
    first, _ = _left_pending(store, built=False)
    second, _ = _left_pending(store, built=False)
    unreadable, readable = _messages(store, first)[-1].id, _messages(store, second)[-1].id

    async def corrupt():
        async with store._connect() as db:
            await db.execute(
                "UPDATE chat_messages SET blocks_json='{not json' WHERE id=?", (unreadable,)
            )
            await db.commit()

    async def stored(message_id: int):
        async with store._connect() as db:
            cursor = await db.execute(
                "SELECT status, blocks_json FROM chat_messages WHERE id=?", (message_id,)
            )
            return tuple(await cursor.fetchone())

    asyncio.run(corrupt())
    settled = asyncio.run(
        store.settle_interrupted_turns(before=datetime.now(UTC).isoformat(), note="x")
    )

    assert sorted(settled) == sorted([unreadable, readable])
    # Kept as they were: reading the reply still reports the corrupt row.
    assert asyncio.run(stored(unreadable)) == ("interrupted", "{not json")


def test_the_sweep_can_be_switched_off_for_more_than_one_process(store, monkeypatch):
    pid, _ = _left_pending(store, built=False)
    monkeypatch.setattr(app_module.settings, "settle_interrupted_on_start", False)

    with TestClient(create_app(store=store)):
        pass

    assert _messages(store, pid)[-1].status == "pending"


def test_a_turn_running_when_its_server_stops_is_settled_interrupted(store, monkeypatch):
    reached, release = threading.Event(), threading.Event()
    provider = ScriptedProvider(
        [
            _tool_turn(tool_use_id="tu-1", name="generate_model", tool_input={"spec": "a cube"}),
            _text_turn("never sent"),
        ]
    )
    _install(monkeypatch, provider, HeldPipeline(reached=reached, release=release))
    monkeypatch.setattr(app_module.settings, "chat_shutdown_grace_secs", 0.2)

    live = LiveServer(create_app(store=store)).start()
    try:
        pid = _new_project(live)
        post_then_drop(
            live.port, f"/projects/{pid}/chat", {"message": "make a cube"}, drop_when=reached
        )
        started = time.monotonic()
        live.stop()
        stopping_took = time.monotonic() - started
    finally:
        release.set()

    assert live.stopped
    # The drain gave up after its grace, rather than waiting on the build it held.
    assert stopping_took < SETTLE_S
    _, reply = _messages(store, pid)
    assert reply.status == "interrupted"
    assert reply.error == INTERRUPTED_NOTE
    # The thread outlived its task: once the build it held returns, it keeps
    # nothing, calls no model, and removes the staging directory itself.
    assert _wait_for(lambda: not _staging_left(store)), _staging_left(store)
    assert len(provider.calls) == 1
    assert _current_version_id(store, pid) is None


def test_a_turn_that_finishes_within_the_shutdown_grace_keeps_its_reply(store, monkeypatch):
    """A shutdown lets a turn near its end finish, rather than cutting it short."""
    reached, release = threading.Event(), threading.Event()
    provider = ScriptedProvider(
        [
            _tool_turn(tool_use_id="tu-1", name="generate_model", tool_input={"spec": "a cube"}),
            _text_turn("Done."),
        ]
    )
    _install(monkeypatch, provider, HeldPipeline(reached=reached, release=release))
    monkeypatch.setattr(app_module.settings, "chat_shutdown_grace_secs", SETTLE_S)
    draining = _draining(monkeypatch)

    live = LiveServer(create_app(store=store)).start()
    try:
        pid = _new_project(live)
        post_then_drop(
            live.port, f"/projects/{pid}/chat", {"message": "make a cube"}, drop_when=reached
        )
        stopper = threading.Thread(target=live.stop, daemon=True)
        stopper.start()
        assert draining.wait(SETTLE_S), "the shutdown never began draining"
        release.set()
        stopper.join(SETTLE_S * 2)
    finally:
        release.set()

    assert live.stopped
    _, reply = _messages(store, pid)
    assert reply.status == "ok"
    assert reply.version_id is not None
    assert reply.version_id == _current_version_id(store, pid)


def test_a_settle_waits_for_a_build_the_thread_is_still_keeping(store, monkeypatch):
    """A shutdown can cut a turn short while its thread is keeping a build. The
    settle waits for that write, so the reply links the version the project is left
    on rather than the one before it."""
    keeping, settling, release = threading.Event(), threading.Event(), threading.Event()
    keep, settle = chat._persist_tool_version, chat._settle_unfinished

    async def held_keep(*args, **kwargs):
        keeping.set()
        await _until(release)
        return await keep(*args, **kwargs)

    async def noticing_settle(*args, **kwargs):
        settling.set()
        await settle(*args, **kwargs)

    provider = ScriptedProvider(
        [
            _tool_turn(tool_use_id="tu-1", name="generate_model", tool_input={"spec": "a cube"}),
            _text_turn("never sent"),
        ]
    )
    _install(monkeypatch, provider)
    monkeypatch.setattr(chat, "_persist_tool_version", held_keep)
    monkeypatch.setattr(chat, "_settle_unfinished", noticing_settle)
    monkeypatch.setattr(app_module.settings, "chat_shutdown_grace_secs", 4.0)

    live = LiveServer(create_app(store=store)).start()
    try:
        pid = _new_project(live)
        post_then_drop(
            live.port, f"/projects/{pid}/chat", {"message": "make a cube"}, drop_when=keeping
        )
        stopper = threading.Thread(target=live.stop, daemon=True)
        stopper.start()
        assert settling.wait(SETTLE_S), "the shutdown never cut the turn short"
        # Long enough for a settle that did not wait to have finished.
        time.sleep(0.5)
        release.set()
        stopper.join(SETTLE_S * 2)
    finally:
        release.set()

    _, reply = _messages(store, pid)
    assert reply.status == "interrupted"
    assert reply.version_id is not None
    assert reply.version_id == _current_version_id(store, pid)


def test_a_shutdown_while_a_failure_is_recorded_settles_the_turn_interrupted(store, monkeypatch):
    """The cancel lands inside the handler recording the failure, where the clause
    that marks a turn interrupted never sees it."""
    recording, release = threading.Event(), threading.Event()
    reverts = 0
    revert = chat._revert_to_last_ok

    class FailingProvider(FakeChatProvider):
        def stream_turn(self, **kwargs):
            raise RuntimeError("the provider failed")
            yield  # pragma: no cover

    async def held_first_revert(store_, project_id):
        nonlocal reverts
        reverts += 1
        if reverts == 1:
            recording.set()
            await _until(release)
        return await revert(store_, project_id)

    _install(monkeypatch, FailingProvider())
    monkeypatch.setattr(chat, "_revert_to_last_ok", held_first_revert)
    # Long enough for the settle on a loaded machine: the drain waits this again
    # for it once it has cancelled the turn.
    monkeypatch.setattr(app_module.settings, "chat_shutdown_grace_secs", 2.0)

    live = LiveServer(create_app(store=store)).start()
    try:
        pid = _new_project(live)
        post_then_drop(
            live.port, f"/projects/{pid}/chat", {"message": "make a cube"}, drop_when=recording
        )
        live.stop()
    finally:
        release.set()

    _, reply = _messages(store, pid)
    assert reply.status == "interrupted"
    assert reply.error == INTERRUPTED_NOTE
