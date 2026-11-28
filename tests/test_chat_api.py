"""Chat SSE turn endpoint tests.

`POST /projects/{id}/chat` runs the provider-agnostic agent loop and streams UI
events mapped from neutral StreamEvents. Every test is driven by the offline
:class:`FakeChatProvider` (no network, no AWS): the provider is scripted, the CAD
pipeline is stubbed, so what's under test is the neutral->UI event mapping, the
nesting of pipeline `stage` events inside `tool_progress`, and turn persistence
(pending -> ok/error, no dangling pending).
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import backend.routers.chat as chat
from backend.app import create_app
from cadless.llm.providers import StreamChunk
from cadless.llm.providers.fake import FakeChatProvider
from cadless.store import Store
from tests.catalog_helpers import load_catalog_item
from tests.chat_api_helpers import (
    ScriptedProvider,
    SequencedPipeline,
    StubPipeline,
    _clarify_turn,
    _install,
    _kb_entries,
    _messages,
    _plan_turn,
    _stream_chat,
    _text_turn,
    _thinking_turn,
    _tool_turn,
)


@pytest.fixture
def store(tmp_path):
    return Store(db_path=tmp_path / "db.sqlite", artifacts_dir=tmp_path / "artifacts")


@pytest.fixture
def client(store):
    with TestClient(create_app(store=store)) as c:
        yield c


# --- tests ------------------------------------------------------------------


def test_chat_rejects_catalog_item_403(client, store, tmp_path):
    """A chat turn persists a new version and moves the project's current
    pointer, so catalog items must refuse it like rerun/reparametrize do. No
    provider is installed here: the gate sits ahead of the credentials check,
    so reaching a 403 is itself the proof that nothing ran."""
    pid = load_catalog_item(store, tmp_path / "cat")
    before = client.get(f"/projects/{pid}").json()["current_version_id"]
    transcript = len(_messages(store, pid))  # the loader seeds one per step

    r = client.post(f"/projects/{pid}/chat", json={"message": "make it taller"})

    assert r.status_code == 403
    assert "read-only" in r.json()["detail"].lower()
    assert client.get(f"/projects/{pid}").json()["current_version_id"] == before
    assert len(_messages(store, pid)) == transcript  # no turn was recorded


def test_text_only_turn_streams_mapped_sequence(client, store, monkeypatch):
    _install(monkeypatch, ScriptedProvider([_text_turn("Hi there!")]))
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    events = _stream_chat(client, pid, "hello")
    kinds = [e["event"] for e in events]
    assert kinds[0] == "turn_start"
    assert "text_delta" in kinds
    assert any(e["event"] == "text_delta" and e["text"] == "Hi there!" for e in events)
    assert kinds[-1] == "turn_end"
    end = next(e for e in events if e["event"] == "turn_end")
    assert end["stop_reason"] == "end_turn"


_CHAT_PARTS = [
    {"index": 0, "name": "chassis", "path": ["car"], "color": None},
    {"index": 1, "name": "wheel", "path": ["car"], "color": [1.0, 0.0, 0.0, 1.0]},
]


class _NamedPartsPipeline(StubPipeline):
    def run(self, intent, export_dir=None, **kwargs):
        result = super().run(intent, export_dir=export_dir, **kwargs)
        for n in range(2):
            (Path(export_dir) / f"model_p{n}.stl").write_bytes(bytes([n]) * 84)
        result.parts = _CHAT_PARTS
        return result


def test_a_chat_built_version_keeps_its_part_names_out_of_the_model_s_view(
    client, store, monkeypatch
):
    """The chat layer stores the parts with the version; the model reading the
    tool's result is not handed one entry per part on every turn."""
    provider = ScriptedProvider(
        [
            _tool_turn(tool_use_id="tu-1", name="generate_model", tool_input={"spec": "a car"}),
            _text_turn("Built your car."),
        ]
    )
    _install(monkeypatch, provider, pipeline=_NamedPartsPipeline())
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    events = _stream_chat(client, pid)

    tresult = next(e for e in events if e["event"] == "tool_result")
    assert client.get(f"/versions/{tresult['version_id']}").json()["parts"] == _CHAT_PARTS
    results = [
        json.loads(block.content)
        for call in provider.calls
        for message in call["messages"]
        for block in message.content
        if block.kind == "tool_result"
    ]
    assert results, "the model was never shown the tool's result"
    assert all("parts" not in result for result in results)


def test_tool_turn_streams_tool_events_with_nested_stage_progress(client, store, monkeypatch):
    provider = ScriptedProvider(
        [
            _tool_turn(tool_use_id="tu-1", name="generate_model", tool_input={"spec": "a cube"}),
            _text_turn("Done — built your cube."),
        ]
    )
    _install(monkeypatch, provider)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    events = _stream_chat(client, pid)
    kinds = [e["event"] for e in events]
    assert "tool_start" in kinds
    assert "tool_progress" in kinds
    assert "tool_result" in kinds

    tstart = next(e for e in events if e["event"] == "tool_start")
    assert tstart["tool"] == "generate_model"
    assert tstart.get("label")

    # Pipeline stage events are NESTED inside tool_progress (StagedProgress reuse).
    progress = [e for e in events if e["event"] == "tool_progress"]
    nested = [e["stage"] for e in progress if "stage" in e]
    assert any(s.get("event") == "stage" and s.get("phase") == "build" for s in nested)

    tresult = next(e for e in events if e["event"] == "tool_result")
    assert tresult["ok"] is True
    assert tresult["version_id"] is not None
    assert tresult["thumbnail"] is not None
    assert tresult["metrics"]["volume"] == 1000.0


def test_successful_tool_turn_persists_blocks_and_links_version(client, store, monkeypatch):
    provider = ScriptedProvider(
        [
            _tool_turn(tool_use_id="tu-1", name="generate_model", tool_input={"spec": "a cube"}),
            _text_turn("Done."),
        ]
    )
    _install(monkeypatch, provider)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    events = _stream_chat(client, pid)
    tresult = next(e for e in events if e["event"] == "tool_result")

    msgs = _messages(store, pid)
    assert [m.role for m in msgs] == ["user", "assistant"]
    assert msgs[0].content == "make a cube"
    assistant = msgs[1]
    assert assistant.status == "ok"
    assert assistant.version_id == tresult["version_id"]
    # Neutral blocks were persisted (text + tool_use + tool_result at least).
    kinds = {b.kind for b in assistant.blocks}
    assert "tool_use" in kinds and "tool_result" in kinds and "text" in kinds

    # The version is real and is now the project's current version.
    assert client.get(f"/projects/{pid}").json()["current_version_id"] == tresult["version_id"]


def test_failed_tool_turn_settles_assistant_to_error_no_dangling_pending(
    client, store, monkeypatch
):
    provider = ScriptedProvider(
        [
            _tool_turn(tool_use_id="tu-1", name="generate_model", tool_input={"spec": "x"}),
            _text_turn("Sorry, that failed."),
        ]
    )
    _install(monkeypatch, provider, pipeline=StubPipeline(ok=False, error="execution: boom"))
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    events = _stream_chat(client, pid)
    tresult = next(e for e in events if e["event"] == "tool_result")
    assert tresult["ok"] is False

    msgs = _messages(store, pid)
    assert [m.role for m in msgs] == ["user", "assistant"]
    assert (msgs[1].status, msgs[1].error) == ("error", "a tool call failed")


def _builds(monkeypatch, outcomes):
    """A turn of one build per outcome, in order, then a closing reply."""
    turns = [
        # Each with its own input, or a call is refused as a repeat of an earlier one.
        _tool_turn(
            tool_use_id=f"tu-{n}", name="generate_model", tool_input={"spec": f"a shelf, try {n}"}
        )
        for n, _ in enumerate(outcomes, 1)
    ]
    _install(
        monkeypatch,
        ScriptedProvider([*turns, _text_turn("Here it is.")]),
        pipeline=SequencedPipeline([(ok, None if ok else "execution: boom") for ok in outcomes]),
    )


def test_a_turn_that_recovers_from_a_failed_build_is_saved_as_ok(client, store, monkeypatch):
    """The turn is what it ended on.

    The failed attempt keeps its own tool result in the message, so marking the
    whole turn failed for it says the kept build failed.
    """
    _builds(monkeypatch, [False, True])
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    events = _stream_chat(client, pid)
    assert [e["ok"] for e in events if e["event"] == "tool_result"] == [False, True]

    turn = _messages(store, pid)[1]
    assert (turn.status, turn.error) == ("ok", None)


def test_provider_exception_emits_error_and_settles_pending(client, store, monkeypatch):
    class BoomProvider(FakeChatProvider):
        def stream_turn(self, **kwargs):
            raise RuntimeError("kaboom")
            yield  # pragma: no cover

    _install(monkeypatch, BoomProvider())
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    events = _stream_chat(client, pid)
    assert any(e["event"] == "error" for e in events)
    err = next(e for e in events if e["event"] == "error")
    assert "kaboom" in (err.get("detail") or "")

    msgs = _messages(store, pid)
    # No dangling pending turn: the assistant message settled to error.
    assert [m.role for m in msgs] == ["user", "assistant"]
    assert msgs[1].status == "error"
    assert msgs[1].status != "pending"


def test_provider_exception_is_logged_server_side(client, store, monkeypatch, caplog):
    """The stream must not be the only place a failed turn is visible.

    The access log records 200 for a chat POST however the stream ends, so a
    turn dying in the provider left nothing behind for anyone reading server
    logs — which is how a total generation outage read as "nothing happens"
    rather than as an error, for a day.
    """

    class BoomProvider(FakeChatProvider):
        def stream_turn(self, **kwargs):
            raise RuntimeError("kaboom")
            yield  # pragma: no cover

    _install(monkeypatch, BoomProvider())
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    with caplog.at_level(logging.ERROR, logger=chat.logger.name):
        _stream_chat(client, pid)

    failures = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert failures, "a provider failure left nothing in the server log"
    assert any(r.exc_info is not None for r in failures), "logged without the traceback"
    assert any(str(pid) in r.getMessage() for r in failures), "logged without naming the project"


def test_aborted_stream_leaves_no_dangling_pending(client, store, monkeypatch, caplog):
    provider = ScriptedProvider(
        [
            _tool_turn(tool_use_id="tu-1", name="generate_model", tool_input={"spec": "a cube"}),
            _text_turn("Done."),
        ]
    )
    _install(monkeypatch, provider)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    # Open the stream and stop reading it before it ends (the client stops reading).
    # The turn must still settle — never left ``pending``.
    with client.stream("POST", f"/projects/{pid}/chat", json={"message": "x"}) as r:
        assert r.status_code == 200
        next(r.iter_text())  # read the first chunk, then drop the connection

    msgs = _messages(store, pid)
    assert [m.role for m in msgs] == ["user", "assistant"]
    assert msgs[1].status != "pending"

    # This turn runs to completion and the client merely stops reading, so
    # nothing on its path is a failure and nothing on it may reach the error
    # log. A log that cries wolf is how the one error that matters — the test
    # above puts one there — gets scrolled past.
    assert not [
        r for r in caplog.records if r.levelno >= logging.ERROR and r.name == chat.logger.name
    ]


def test_clarification_turn_emits_event_persists_block_and_reloads(client, store, monkeypatch):
    questions = [
        {"text": "Metric or imperial?", "options": ["mm", "in"]},
        {"text": "Through-hole or blind?"},
    ]
    provider = ScriptedProvider(
        [
            _clarify_turn(tool_use_id="tu-1", questions=questions),
            # Scripted but must never run: clarification ends the turn.
            _text_turn("should not be reached"),
        ]
    )
    _install(monkeypatch, provider)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    events = _stream_chat(client, pid, "make a bolt")
    kinds = [e["event"] for e in events]
    # A `clarification` SSE event is emitted, carrying the questions[].
    assert "clarification" in kinds
    clar = next(e for e in events if e["event"] == "clarification")
    assert [q["text"] for q in clar["questions"]] == [
        "Metric or imperial?",
        "Through-hole or blind?",
    ]
    assert clar["questions"][0]["options"] == ["mm", "in"]
    # The turn ended on clarification (terminal, not auto-continued).
    end = next(e for e in events if e["event"] == "turn_end")
    assert end["stop_reason"] == "clarification"

    # Persisted: the assistant turn settled (not pending) with a clarification block.
    msgs = _messages(store, pid)
    assert [m.role for m in msgs] == ["user", "assistant"]
    assert msgs[1].status != "pending"
    block_kinds = {b.kind for b in msgs[1].blocks}
    assert "clarification" in block_kinds

    # Reload restores the questions/chips via GET /projects/{id}/messages.
    reloaded = client.get(f"/projects/{pid}/messages").json()
    assistant = reloaded[-1]
    clar_block = next(b for b in assistant["blocks"] if b["kind"] == "clarification")
    restored = clar_block["input"]["questions"]
    assert [q["text"] for q in restored] == [
        "Metric or imperial?",
        "Through-hole or blind?",
    ]


def test_clarification_with_more_than_three_questions_is_truncated(client, store, monkeypatch):
    five = [{"text": f"Q{i}?"} for i in range(5)]
    provider = ScriptedProvider([_clarify_turn(tool_use_id="tu-1", questions=five)])
    _install(monkeypatch, provider)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    events = _stream_chat(client, pid, "ambiguous")
    clar = next(e for e in events if e["event"] == "clarification")
    assert len(clar["questions"]) == 3


def _seed_messages(store, pid, n):
    """Seed a session with ``n`` user/assistant messages (a long transcript)."""
    import asyncio

    async def _go():
        sess = await store.get_or_create_session(pid)
        for i in range(n):
            role = "user" if i % 2 == 0 else "assistant"
            await store.add_message(sess.id, role, f"old message {i}")

    asyncio.run(_go())


def test_long_session_history_is_compacted_before_reaching_provider(client, store, monkeypatch):
    """Session hygiene: a long transcript is folded into a bounded
    rolling synopsis + recent verbatim tail before the agent's stream_turn sees it,
    while the script_versions chain is left untouched."""
    from cadless.config import settings as cfg

    provider = ScriptedProvider([_text_turn("Synopsis: building a widget.")])
    _install(monkeypatch, provider)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    # Seed well over the compaction threshold (default 40 messages).
    seeded = cfg.transcript_compact_threshold + 20
    versions_before = client.get(f"/projects/{pid}/versions").status_code
    _seed_messages(store, pid, seeded)

    _stream_chat(client, pid, "now do the next thing")

    # The agent's stream_turn received a bounded, compacted history: a single
    # synopsis message plus the recent verbatim tail — not the whole transcript.
    # (The summariser's one-shot `complete` also routes through stream_turn with an
    # empty message list; the agent turn is the one carrying the real history.)
    agent_call = next(c for c in provider.calls if c["messages"] and c["messages"][0].content)
    sent = agent_call["messages"]
    # bounded: the compacted history (synopsis + recent tail) plus the live turn's
    # own user message — far below the seeded transcript length.
    assert len(sent) <= 1 + cfg.transcript_keep_recent + 1
    assert len(sent) < seeded
    # The first message is the rolling synopsis built from the summariser output.
    assert sent[0].role == "user"
    assert "[Earlier conversation summary]" in sent[0].content[0].text

    # The persisted transcript (the durable message chain) is NOT rewritten by
    # compaction — every seeded message is still there (plus the new turn's two).
    msgs = _messages(store, pid)
    assert len(msgs) >= seeded
    # And the versions endpoint is still reachable (script_versions chain intact).
    assert client.get(f"/projects/{pid}/versions").status_code == versions_before


def test_short_session_history_reaches_provider_unchanged(client, store, monkeypatch):
    """Purely additive: a session below the threshold is replayed verbatim (no
    synopsis, no extra summariser call)."""
    provider = ScriptedProvider([_text_turn("Hi.")])
    _install(monkeypatch, provider)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    _seed_messages(store, pid, 4)  # well below the threshold
    _stream_chat(client, pid, "hello again")

    sent = provider.calls[0]["messages"]
    # No synopsis injected: the seeded messages are present verbatim.
    assert all(
        "[Earlier conversation summary]" not in (b.text or "") for m in sent for b in m.content
    )
    assert any("old message 0" in (b.text or "") for m in sent for b in m.content)


def test_thinking_turn_streams_thinking_delta_and_persists_verbatim_block(
    client, store, monkeypatch
):
    _install(monkeypatch, ScriptedProvider([_thinking_turn("reasoning", "Done.")]))
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    events = _stream_chat(client, pid, "make a cube")
    kinds = [e["event"] for e in events]
    # A `thinking_delta` SSE event is emitted carrying the reasoning text.
    assert "thinking_delta" in kinds
    delta = next(e for e in events if e["event"] == "thinking_delta")
    assert delta["text"] == "reasoning"

    # Persisted: the assistant turn carries a verbatim `thinking` block whose
    # provider_raw (signature included) round-trips unchanged.
    msgs = _messages(store, pid)
    assistant = msgs[1]
    thinking = [b for b in assistant.blocks if b.kind == "thinking"]
    assert len(thinking) == 1
    assert thinking[0].provider == "bedrock"
    assert thinking[0].provider_raw == {
        "reasoningContent": {"reasoningText": {"text": "reasoning", "signature": "S"}}
    }

    # Reload via GET /messages also exposes the thinking block + its text.
    reloaded = client.get(f"/projects/{pid}/messages").json()
    a = reloaded[-1]
    tb = next(b for b in a["blocks"] if b["kind"] == "thinking")
    assert tb["text"] == "reasoning"


def test_plan_event_streams_before_tool_start_and_persists_block(client, store, monkeypatch):
    provider = ScriptedProvider(
        [
            _plan_turn(tool_use_id="tu-0", steps=["base plate", "bolt circle", "fillets"]),
            _tool_turn(tool_use_id="tu-1", name="generate_model", tool_input={"spec": "a flange"}),
            _text_turn("Done — built your flange."),
        ]
    )
    _install(monkeypatch, provider)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    events = _stream_chat(client, pid, "make a flange")
    kinds = [e["event"] for e in events]
    # A `plan` SSE event is emitted carrying the ordered steps[].
    assert "plan" in kinds
    plan = next(e for e in events if e["event"] == "plan")
    assert plan["steps"] == ["base plate", "bolt circle", "fillets"]
    # Ordering: the plan event precedes the action card's tool_start.
    assert kinds.index("plan") < kinds.index("tool_start")

    # Persisted: the assistant turn carries a `plan` block reload restores.
    msgs = _messages(store, pid)
    assistant = msgs[1]
    assert assistant.status != "pending"
    plan_blocks = [b for b in assistant.blocks if b.kind == "plan"]
    assert len(plan_blocks) == 1
    assert plan_blocks[0].input["steps"] == ["base plate", "bolt circle", "fillets"]

    reloaded = client.get(f"/projects/{pid}/messages").json()
    a = reloaded[-1]
    pb = next(b for b in a["blocks"] if b["kind"] == "plan")
    assert pb["input"]["steps"] == ["base plate", "bolt circle", "fillets"]


def test_planned_turn_annotates_version_with_active_plan_step(client, store, monkeypatch):
    """A tool call that runs after a submitted plan stamps its persisted version
    with the active 1-based plan-step pointer, so the UI can narrate
    'rolled back to step N'."""
    provider = ScriptedProvider(
        [
            _plan_turn(tool_use_id="tu-0", steps=["base plate", "bolt circle"]),
            _tool_turn(tool_use_id="tu-1", name="generate_model", tool_input={"spec": "a flange"}),
            _text_turn("Done."),
        ]
    )
    _install(monkeypatch, provider)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    events = _stream_chat(client, pid, "make a flange")
    tresult = next(e for e in events if e["event"] == "tool_result")
    vid = tresult["version_id"]
    assert vid is not None
    payload = client.get(f"/versions/{vid}").json()
    assert payload["plan_step"] == 1


def test_unplanned_turn_leaves_plan_step_null(client, store, monkeypatch):
    """A trivial turn with no submitted plan persists a version whose plan_step is
    null and behaves exactly as today."""
    provider = ScriptedProvider(
        [
            _tool_turn(tool_use_id="tu-1", name="generate_model", tool_input={"spec": "a cube"}),
            _text_turn("Done."),
        ]
    )
    _install(monkeypatch, provider)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    events = _stream_chat(client, pid, "make a cube")
    tresult = next(e for e in events if e["event"] == "tool_result")
    vid = tresult["version_id"]
    assert vid is not None
    assert client.get(f"/versions/{vid}").json()["plan_step"] is None


def test_trivial_turn_omits_plan_without_error(client, store, monkeypatch):
    provider = ScriptedProvider(
        [
            _tool_turn(tool_use_id="tu-1", name="generate_model", tool_input={"spec": "a cube"}),
            _text_turn("Done."),
        ]
    )
    _install(monkeypatch, provider)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    events = _stream_chat(client, pid, "make a cube")
    assert all(e["event"] != "plan" for e in events)
    msgs = _messages(store, pid)
    assert msgs[1].status == "ok"
    assert all(b.kind != "plan" for b in msgs[1].blocks)


def test_unknown_project_404(client, monkeypatch):
    _install(monkeypatch, ScriptedProvider([_text_turn("hi")]))
    r = client.post("/projects/9999/chat", json={"message": "hi"})
    assert r.status_code == 404


# --- mid-run message queuing / steer -----------------------------


class GatedProvider(FakeChatProvider):
    """A scripted provider that pauses *between* turns until a gate is released.

    Lets a test POST a steer message *while* the first `/chat` turn is mid-loop:
    the loop runs turn 0 (a tool call), and at the tail of that turn's stream this
    provider opens ``gate`` (signalling "mid-flight, before the next boundary")
    and waits on ``release``. The test, woken by ``gate``, POSTs the steer and
    sets ``release``, so the steer is queued BEFORE the next turn's boundary-drain
    runs — making the injection deterministic.
    """

    def __init__(
        self, turns: Sequence[list[StreamChunk]], gate: threading.Event, release: threading.Event
    ) -> None:
        super().__init__()
        self._turns = list(turns)
        self._i = 0
        self._gate = gate
        self._release = release

    def stream_turn(self, **kwargs) -> Iterator[StreamChunk]:
        kwargs = {**kwargs, "messages": list(kwargs["messages"])}
        self.calls.append(kwargs)
        i = self._i
        self._i += 1
        turn = self._turns[min(i, len(self._turns) - 1)]
        yield from turn
        if i == 0:
            # Turn 0 fully streamed; pause before the loop drains for turn 1 so the
            # test can queue a steer that the upcoming boundary is guaranteed to see.
            self._gate.set()
            self._release.wait(timeout=5)


def test_steer_message_injected_into_in_flight_turn(client, store, monkeypatch):
    gate = threading.Event()
    release = threading.Event()
    provider = GatedProvider(
        [
            _tool_turn(tool_use_id="tu-1", name="generate_model", tool_input={"spec": "a"}),
            _text_turn("Built — and made it red."),
        ],
        gate,
        release,
    )
    _install(monkeypatch, provider)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    captured: dict = {}

    def post_steer() -> None:
        gate.wait(timeout=5)  # wait until the loop is mid-flight at the boundary
        r = client.post(f"/projects/{pid}/chat/steer", json={"message": "make it red"})
        captured["status"] = r.status_code
        release.set()

    t = threading.Thread(target=post_steer)
    t.start()
    events = _stream_chat(client, pid, "make a part")
    t.join()

    assert captured["status"] == 202
    # The 2nd provider call saw the steer text in its messages (injected mid-run).
    second = provider.calls[1]
    steer_seen = any(
        b.kind == "text" and "make it red" in (b.text or "")
        for m in second["messages"]
        if m.role == "user"
        for b in m.content
    )
    assert steer_seen
    # The turn streamed a `steer` UI event carrying the queued text.
    steer_evs = [e for e in events if e["event"] == "steer"]
    assert steer_evs and steer_evs[0]["text"] == "make it red"


def test_steer_message_persisted_in_order(client, store, monkeypatch):
    gate = threading.Event()
    release = threading.Event()
    provider = GatedProvider(
        [
            _tool_turn(tool_use_id="tu-1", name="generate_model", tool_input={"spec": "a"}),
            _text_turn("Done."),
        ],
        gate,
        release,
    )
    _install(monkeypatch, provider)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    def post_steer() -> None:
        gate.wait(timeout=5)
        client.post(f"/projects/{pid}/chat/steer", json={"message": "steer here"})
        release.set()

    t = threading.Thread(target=post_steer)
    t.start()
    _stream_chat(client, pid, "make a part")
    t.join()

    msgs = _messages(store, pid)
    # Order: the initial user message, then the assistant turn whose persisted
    # blocks carry the injected steer text (in order, after the tool_result).
    assert msgs[0].role == "user" and msgs[0].content == "make a part"
    assistant = msgs[-1]
    assert assistant.role == "assistant"
    assert assistant.status != "pending"
    block_texts = [b.text for b in assistant.blocks if b.kind == "text"]
    assert any("steer here" in (t or "") for t in block_texts)
    tool_result_idx = next(i for i, b in enumerate(assistant.blocks) if b.kind == "tool_result")
    steer_idx = next(
        i
        for i, b in enumerate(assistant.blocks)
        if b.kind == "text" and "steer here" in (b.text or "")
    )
    assert tool_result_idx < steer_idx


def test_steer_unknown_project_404(client, monkeypatch):
    _install(monkeypatch, ScriptedProvider([_text_turn("hi")]))
    r = client.post("/projects/9999/chat/steer", json={"message": "x"})
    assert r.status_code == 404


# --- auto-distill flywheel --------------------------------------


def test_ok_turn_auto_distills_one_kb_entry(client, store, monkeypatch):
    """An ok+asserted turn auto-writes exactly one KB entry; code stored, vector
    is over intent+feature-signature (not the raw code)."""
    from cadless.distill import feature_tags, signature_text

    provider = ScriptedProvider(
        [
            _tool_turn(tool_use_id="tu-1", name="generate_model", tool_input={"spec": "a cube"}),
            _text_turn("Done."),
        ]
    )
    _install(monkeypatch, provider)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    _stream_chat(client, pid, "make a cube")

    entries = _kb_entries(store)
    assert len(entries) == 1
    e = entries[0]
    assert e.nl_intent == "make a cube"
    # StubPipeline's code is stored verbatim on the entry.
    assert "result = None" in e.code
    # Embedding is over intent + feature signature, NOT the raw code.
    tags = feature_tags(
        e.code, {"bbox": [10, 10, 10], "volume": 1000.0, "parameters": {"size": 10}}
    )
    expected = provider.embed(signature_text("make a cube", tags))
    assert e.embedding == expected
    assert e.embedding != provider.embed(e.code)
    assert e.geometry_signature.get("feature_tags") == tags


def test_failed_turn_distills_nothing(client, store, monkeypatch):
    provider = ScriptedProvider(
        [
            _tool_turn(tool_use_id="tu-1", name="generate_model", tool_input={"spec": "x"}),
            _text_turn("Sorry, that failed."),
        ]
    )
    _install(monkeypatch, provider, pipeline=StubPipeline(ok=False, error="execution: boom"))
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    _stream_chat(client, pid)
    assert _kb_entries(store) == []


def test_a_recovered_turn_distills_the_build_it_ended_on(client, store, monkeypatch):
    """A repaired turn ends on a good build like any other ok turn, so it teaches,
    and what it teaches is that build, not an earlier one it moved past."""
    _builds(monkeypatch, [True, False, True])
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    events = _stream_chat(client, pid, "make a shelf")
    builds = [e for e in events if e["event"] == "tool_result"]
    assert [e["ok"] for e in builds] == [True, False, True]

    entries = _kb_entries(store)
    assert [e.provenance["version_id"] for e in entries] == [builds[-1]["version_id"]]


def test_a_turn_ending_on_a_failure_after_a_kept_build_distills_nothing(client, store, monkeypatch):
    """Its earlier build is still the last good one, but the turn did not end on it."""
    _builds(monkeypatch, [True, False])
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    events = _stream_chat(client, pid)
    assert [e["ok"] for e in events if e["event"] == "tool_result"] == [True, False]

    assert _kb_entries(store) == []


def test_text_only_turn_distills_nothing(client, store, monkeypatch):
    """A turn with no produced version (text only) has nothing to distill."""
    _install(monkeypatch, ScriptedProvider([_text_turn("Hi there!")]))
    pid = client.post("/projects", json={"name": "P"}).json()["id"]
    _stream_chat(client, pid, "hello")
    assert _kb_entries(store) == []


def test_distill_failure_does_not_fail_the_turn(client, store, monkeypatch):
    """A distill error is swallowed: the turn still settles ok with its version."""

    class ExplodingEmbedProvider(ScriptedProvider):
        def embed(self, text):
            raise RuntimeError("embed exploded")

    provider = ExplodingEmbedProvider(
        [
            _tool_turn(tool_use_id="tu-1", name="generate_model", tool_input={"spec": "a cube"}),
            _text_turn("Done."),
        ]
    )
    _install(monkeypatch, provider)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    events = _stream_chat(client, pid, "make a cube")
    tresult = next(e for e in events if e["event"] == "tool_result")
    assert tresult["ok"] is True

    # The turn still settled ok and linked its version — distill failure swallowed.
    msgs = _messages(store, pid)
    assert msgs[1].status == "ok"
    assert msgs[1].version_id == tresult["version_id"]
    assert _kb_entries(store) == []


# --- dynamic RAG grounding wiring --------------------------------


def test_turn_retrieves_grounding_and_threads_it_into_generate(client, store, monkeypatch):
    """The live chat path retrieves grounding for the request and hands it to the
    fresh-generation pipeline call (chat -> agent -> pipeline.run)."""
    provider = ScriptedProvider(
        [
            _tool_turn(tool_use_id="tu-1", name="generate_model", tool_input={"spec": "a cube"}),
            _text_turn("Done."),
        ]
    )
    pipeline = StubPipeline()
    _install(monkeypatch, provider, pipeline=pipeline)

    captured: dict = {}

    async def fake_retrieve(store_, provider_, *, intent, **kw):
        captured["intent"] = intent
        return "RETRIEVED GROUNDING"

    monkeypatch.setattr(chat, "retrieve_grounding", fake_retrieve)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    _stream_chat(client, pid, "make a cube")

    # Retrieval used the user's NL request as the intent.
    assert captured["intent"] == "make a cube"
    # The grounding reached the fresh-generation pipeline call.
    assert pipeline.groundings == ["RETRIEVED GROUNDING"]


def test_grounding_retrieval_reads_the_pipeline_snapshot(client, store, monkeypatch):
    """Retrieval and generation must run under the same settings.

    The four ``rag_*`` knobs are read inside ``retrieve_grounding``. Left to the
    live singleton they would be re-read there while the pipeline generates from
    its own snapshot, so a save landing mid-turn would split one turn across two
    configurations — which is the thing the snapshot exists to prevent.
    """
    from cadless.config import Settings

    provider = ScriptedProvider(
        [
            _tool_turn(tool_use_id="tu-1", name="generate_model", tool_input={"spec": "a cube"}),
            _text_turn("Done."),
        ]
    )
    pipeline = StubPipeline()
    # Distinguishable from the process-wide singleton, so passing the wrong one
    # cannot pass this test by coincidence.
    pipeline.config = Settings(rag_top_k=41)
    _install(monkeypatch, provider, pipeline=pipeline)

    captured: dict = {}

    async def fake_retrieve(store_, provider_, *, intent, **kw):
        captured.update(kw)
        return ""

    monkeypatch.setattr(chat, "retrieve_grounding", fake_retrieve)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    _stream_chat(client, pid, "make a cube")

    assert captured["config"] is pipeline.config
    assert captured["config"].rag_top_k == 41


def test_grounding_retrieval_failure_does_not_fail_the_turn(client, store, monkeypatch):
    """A grounding-retrieval error is swallowed: the turn still settles ok and the
    generation runs without grounding."""
    provider = ScriptedProvider(
        [
            _tool_turn(tool_use_id="tu-1", name="generate_model", tool_input={"spec": "a cube"}),
            _text_turn("Done."),
        ]
    )
    pipeline = StubPipeline()
    _install(monkeypatch, provider, pipeline=pipeline)

    async def boom_retrieve(*a, **k):
        raise RuntimeError("retrieval exploded")

    monkeypatch.setattr(chat, "retrieve_grounding", boom_retrieve)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    events = _stream_chat(client, pid, "make a cube")
    tresult = next(e for e in events if e["event"] == "tool_result")
    assert tresult["ok"] is True

    msgs = _messages(store, pid)
    assert msgs[1].status == "ok"
    # Generation still ran, with empty grounding (the swallowed-failure default).
    assert pipeline.groundings == [""]
