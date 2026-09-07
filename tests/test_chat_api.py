"""Chat SSE turn endpoint tests.

`POST /projects/{id}/chat` runs the provider-agnostic agent loop and streams UI
events mapped from neutral StreamEvents. Every test is driven by the offline
:class:`FakeChatProvider` (no network, no AWS): the provider is scripted, the CAD
pipeline is stubbed, so what's under test is the neutral->UI event mapping, the
nesting of pipeline `stage` events inside `tool_progress`, and turn persistence
(pending -> ok/error, no dangling pending).
"""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import backend.routers.chat as chat
from backend.app import create_app
from cadless.config import settings
from cadless.llm.providers import StreamChunk
from cadless.llm.providers.fake import FakeChatProvider
from cadless.llm.types import ContentBlock, StreamEvent
from cadless.pipeline import Attempt, GenerationResult
from cadless.store import Store
from tests.catalog_helpers import load_catalog_item

# --- scripted provider turns ------------------------------------------------


def _text_turn(text: str) -> list[StreamChunk]:
    return [
        StreamChunk(StreamEvent.TURN_START),
        StreamChunk(StreamEvent.TEXT_DELTA, {"text": text}),
        StreamChunk(StreamEvent.TURN_DELTA, {"stop_reason": "end_turn"}),
        StreamChunk(StreamEvent.USAGE, {"input_tokens": 10, "output_tokens": 5}),
        StreamChunk(StreamEvent.TURN_STOP),
    ]


def _tool_turn(*, tool_use_id: str, name: str, tool_input: dict) -> list[StreamChunk]:
    block = ContentBlock.of_tool_use(id=tool_use_id, name=name, input=tool_input)
    return [
        StreamChunk(StreamEvent.TURN_START),
        StreamChunk(
            StreamEvent.TOOL_USE_STOP,
            {"id": tool_use_id, "name": name, "input": tool_input, "block": block},
        ),
        StreamChunk(StreamEvent.TURN_DELTA, {"stop_reason": "tool_use"}),
        StreamChunk(StreamEvent.USAGE, {"input_tokens": 10, "output_tokens": 5}),
        StreamChunk(StreamEvent.TURN_STOP),
    ]


def _thinking_turn(thinking: str, text: str) -> list[StreamChunk]:
    block = ContentBlock.of_thinking(
        thinking,
        provider="bedrock",
        provider_raw={"reasoningContent": {"reasoningText": {"text": thinking, "signature": "S"}}},
    )
    return [
        StreamChunk(StreamEvent.TURN_START),
        StreamChunk(StreamEvent.THINKING_DELTA, {"text": thinking}),
        StreamChunk(StreamEvent.THINKING_STOP, {"text": thinking, "block": block}),
        StreamChunk(StreamEvent.TEXT_DELTA, {"text": text}),
        StreamChunk(StreamEvent.TURN_DELTA, {"stop_reason": "end_turn"}),
        StreamChunk(StreamEvent.USAGE, {"input_tokens": 10, "output_tokens": 5}),
        StreamChunk(StreamEvent.TURN_STOP),
    ]


def _clarify_turn(*, tool_use_id: str, questions: list[dict]) -> list[StreamChunk]:
    tool_input = {"questions": questions}
    block = ContentBlock.of_tool_use(id=tool_use_id, name="ask_clarification", input=tool_input)
    return [
        StreamChunk(StreamEvent.TURN_START),
        StreamChunk(
            StreamEvent.TOOL_USE_STOP,
            {
                "id": tool_use_id,
                "name": "ask_clarification",
                "input": tool_input,
                "block": block,
            },
        ),
        StreamChunk(StreamEvent.TURN_DELTA, {"stop_reason": "tool_use"}),
        StreamChunk(StreamEvent.USAGE, {"input_tokens": 10, "output_tokens": 5}),
        StreamChunk(StreamEvent.TURN_STOP),
    ]


def _plan_turn(*, tool_use_id: str, steps: list[str]) -> list[StreamChunk]:
    tool_input = {"steps": steps}
    block = ContentBlock.of_tool_use(id=tool_use_id, name="submit_plan", input=tool_input)
    return [
        StreamChunk(StreamEvent.TURN_START),
        StreamChunk(
            StreamEvent.TOOL_USE_STOP,
            {
                "id": tool_use_id,
                "name": "submit_plan",
                "input": tool_input,
                "block": block,
            },
        ),
        StreamChunk(StreamEvent.TURN_DELTA, {"stop_reason": "tool_use"}),
        StreamChunk(StreamEvent.USAGE, {"input_tokens": 10, "output_tokens": 5}),
        StreamChunk(StreamEvent.TURN_STOP),
    ]


class ScriptedProvider(FakeChatProvider):
    """Returns a different scripted turn on each ``stream_turn`` call."""

    def __init__(self, turns: Sequence[list[StreamChunk]]) -> None:
        super().__init__()
        self._turns = list(turns)
        self._i = 0

    def stream_turn(self, **kwargs) -> Iterator[StreamChunk]:
        kwargs = {**kwargs, "messages": list(kwargs["messages"])}
        self.calls.append(kwargs)
        turn = self._turns[min(self._i, len(self._turns) - 1)]
        self._i += 1
        yield from turn


class StubPipeline:
    """Stand-in CAD pipeline: emits the real stage-event vocabulary, writes a glb."""

    def __init__(self, *, ok: bool = True, error: str | None = None) -> None:
        self.ok = ok
        self.error = error
        self.groundings: list[str | None] = []
        self.images: list[list] = []
        # What the codegen model "wrote down" about the picture, when a test wants one.
        self.reading: str | None = None
        # The real pipeline exposes the settings snapshot its turn runs under, and
        # the chat route hands it to grounding retrieval so both halves of a turn
        # read the same configuration. Modelled here so the stub keeps the same
        # surface — without it the route's retrieval fails into its best-effort
        # branch and the turn silently loses grounding.
        self.config = settings

    def run(
        self,
        intent,
        export_dir=None,
        on_progress=None,
        prior_code=None,
        grounding=None,
        images=(),
        on_reading=None,
    ):
        self.groundings.append(grounding)
        self.images.append(list(images))
        if on_reading is not None and self.reading is not None:
            on_reading(self.reading)
        if on_progress:
            on_progress(
                {
                    "event": "start",
                    "intent": intent,
                    "max_tries": 3,
                    "mode": "refine" if prior_code else "generate",
                }
            )
            on_progress({"event": "stage", "phase": "validate", "status": "ok", "attempt": 1})
            on_progress({"event": "stage", "phase": "build", "status": "ok", "attempt": 1})
        glb = None
        if export_dir and self.ok:
            Path(export_dir).mkdir(parents=True, exist_ok=True)
            glb = str(Path(export_dir) / "model.glb")
            Path(glb).write_bytes(b"glTF\x00")
            (Path(export_dir) / "model.step").write_text("ISO")
        return GenerationResult(
            ok=self.ok,
            intent=intent,
            code='params = {"size": 10}\nresult = None\n' if self.ok else None,
            error=self.error,
            volume=1000.0 if self.ok else None,
            bbox=(10, 10, 10) if self.ok else None,
            glb_path=glb,
            step_path=str(Path(export_dir) / "model.step") if (export_dir and self.ok) else None,
            parameters={"size": 10} if self.ok else {},
            attempts=[],
        )


@pytest.fixture
def store(tmp_path):
    return Store(db_path=tmp_path / "db.sqlite", artifacts_dir=tmp_path / "artifacts")


@pytest.fixture
def client(store):
    with TestClient(create_app(store=store)) as c:
        yield c


def _install(monkeypatch, provider, pipeline=None):
    """Wire the scripted provider + stubbed pipeline into the chat router."""
    monkeypatch.setattr(chat, "build_provider", lambda *a, **k: provider)
    monkeypatch.setattr(chat, "build_pipeline", lambda *a, **k: pipeline or StubPipeline())
    # The scripted provider needs no real credentials; declare it so the chat
    # credential preflight does not gate these offline turns.
    monkeypatch.setattr(chat.settings, "llm_provider", "fake")


def _events(body: str) -> list[dict]:
    """Parse the SSE body into a list of decoded UI-event dicts."""
    out = []
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            out.append(json.loads(line[len("data:") :].strip()))
    return out


def _stream_chat(client, pid, text="make a cube"):
    with client.stream("POST", f"/projects/{pid}/chat", json={"message": text}) as r:
        assert r.status_code == 200
        return _events("".join(r.iter_text()))


def _messages(store, pid):
    import asyncio

    async def _go():
        sess = await store.get_or_create_session(pid)
        return await store.list_messages(sess.id)

    return asyncio.run(_go())


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
    assert msgs[1].status == "error"
    assert msgs[1].status != "pending"


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


def test_aborted_stream_leaves_no_dangling_pending(client, store, monkeypatch):
    provider = ScriptedProvider(
        [
            _tool_turn(tool_use_id="tu-1", name="generate_model", tool_input={"spec": "a cube"}),
            _text_turn("Done."),
        ]
    )
    _install(monkeypatch, provider)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    # Open the stream and close it early (client disconnect / Stop) before reading
    # to completion. The turn must still settle — never left ``pending``.
    with client.stream("POST", f"/projects/{pid}/chat", json={"message": "x"}) as r:
        assert r.status_code == 200
        next(r.iter_text())  # read the first chunk, then drop the connection

    msgs = _messages(store, pid)
    assert [m.role for m in msgs] == ["user", "assistant"]
    assert msgs[1].status != "pending"


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


import threading  # noqa: E402


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


def _kb_entries(store):
    import asyncio

    return asyncio.run(store.list_kb_entries())


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


# --- forge mode: opt-in toggle + both-true gate (C4) ---------------


class ForgePipeline(StubPipeline):
    """StubPipeline that also implements run_candidates for the best-of-N race.

    ``run_candidates`` returns N candidates: the FIRST is an ok winner (with a real
    glb), the rest are failing losers. Records whether the single-run path or the
    race path was taken so the gate can be asserted.
    """

    def __init__(self) -> None:
        super().__init__(ok=True)
        self.run_count = 0
        self.candidate_ns: list[int | None] = []

    def run(
        self,
        intent,
        export_dir=None,
        on_progress=None,
        prior_code=None,
        grounding=None,
        temperature=None,
        images=(),
        on_reading=None,
    ):
        self.run_count += 1
        return super().run(
            intent,
            export_dir=export_dir,
            on_progress=on_progress,
            prior_code=prior_code,
            grounding=grounding,
            images=images,
            on_reading=on_reading,
        )

    def run_candidates(
        self,
        intent,
        n=None,
        export_dir=None,
        assertions=None,
        grounding=None,
        temperature=None,
        images=(),
        on_reading=None,
    ):
        self.candidate_ns.append(n)
        winner = super().run(
            intent,
            export_dir=export_dir,
            grounding=grounding,
            images=images,
            on_reading=on_reading,
        )
        losers = [
            GenerationResult(ok=False, intent=intent, code=f"broken-{i}", error="execution: boom")
            for i in range(max(0, (n or 1) - 1))
        ]
        return [winner, *losers]


def _versions(store, pid):
    import asyncio

    return asyncio.run(store.list_versions(pid))


def _stream_forge(client, pid, *, forge: bool, text="make a cube"):
    body = {"message": text, "forge": forge}
    with client.stream("POST", f"/projects/{pid}/chat", json=body) as r:
        assert r.status_code == 200
        return _events("".join(r.iter_text()))


def test_forge_opt_in_with_switch_on_races_and_persists_winner_plus_losers(
    client, store, monkeypatch
):
    """Both gates true: a fresh generate races N, persists the winner as current and
    the losers as non-current candidate rows."""
    monkeypatch.setattr(chat.settings, "forge_enabled", True)
    monkeypatch.setattr(chat.settings, "forge_budget", 6)
    monkeypatch.setattr(chat.settings, "forge_candidate_cost", 2)
    monkeypatch.setattr(chat.settings, "forge_min_n", 2)
    monkeypatch.setattr(chat.settings, "forge_max_n", 5)
    provider = ScriptedProvider(
        [
            _tool_turn(tool_use_id="tu-1", name="generate_model", tool_input={"spec": "a cube"}),
            _text_turn("Done."),
        ]
    )
    pipeline = ForgePipeline()
    _install(monkeypatch, provider, pipeline=pipeline)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    events = _stream_forge(client, pid, forge=True)
    tresult = next(e for e in events if e["event"] == "tool_result")
    assert tresult["ok"] is True

    # The race ran (N = 6//2 = 3); the single-run path was NOT used.
    assert pipeline.candidate_ns == [3]
    assert pipeline.run_count == 0

    # Winner is the project's current version.
    winner_id = tresult["version_id"]
    assert client.get(f"/projects/{pid}").json()["current_version_id"] == winner_id

    # Losers persisted as non-current candidate rows of the winner.
    candidates = asyncio.run(store.list_candidate_versions(winner_id))
    assert len(candidates) == 2
    for c in candidates:
        assert c.candidate_of_version_id == winner_id
        assert c.id != winner_id


def test_forge_opt_in_but_switch_off_does_not_race(client, store, monkeypatch):
    """Per-turn opt-in WITHOUT the global switch => normal single-run path."""
    monkeypatch.setattr(chat.settings, "forge_enabled", False)
    provider = ScriptedProvider(
        [
            _tool_turn(tool_use_id="tu-1", name="generate_model", tool_input={"spec": "a cube"}),
            _text_turn("Done."),
        ]
    )
    pipeline = ForgePipeline()
    _install(monkeypatch, provider, pipeline=pipeline)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    events = _stream_forge(client, pid, forge=True)
    tresult = next(e for e in events if e["event"] == "tool_result")
    assert tresult["ok"] is True

    assert pipeline.candidate_ns == []  # never raced
    assert pipeline.run_count == 1
    # Exactly one version persisted, no candidate rows — today's behavior.
    assert len(_versions(store, pid)) == 1
    assert asyncio.run(store.list_candidate_versions(tresult["version_id"])) == []


def test_forge_switch_on_but_opt_out_does_not_race(client, store, monkeypatch):
    """Global switch on but the turn did NOT opt in => normal single-run path."""
    monkeypatch.setattr(chat.settings, "forge_enabled", True)
    provider = ScriptedProvider(
        [
            _tool_turn(tool_use_id="tu-1", name="generate_model", tool_input={"spec": "a cube"}),
            _text_turn("Done."),
        ]
    )
    pipeline = ForgePipeline()
    _install(monkeypatch, provider, pipeline=pipeline)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    events = _stream_forge(client, pid, forge=False)
    tresult = next(e for e in events if e["event"] == "tool_result")
    assert tresult["ok"] is True

    assert pipeline.candidate_ns == []
    assert pipeline.run_count == 1
    assert len(_versions(store, pid)) == 1


def test_forge_defaults_off_when_flag_omitted(client, store, monkeypatch):
    """Omitting the forge flag entirely behaves as opt-out even with the switch on."""
    monkeypatch.setattr(chat.settings, "forge_enabled", True)
    provider = ScriptedProvider(
        [
            _tool_turn(tool_use_id="tu-1", name="generate_model", tool_input={"spec": "a cube"}),
            _text_turn("Done."),
        ]
    )
    pipeline = ForgePipeline()
    _install(monkeypatch, provider, pipeline=pipeline)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    # No "forge" key in the body at all.
    events = _stream_chat(client, pid, "make a cube")
    tresult = next(e for e in events if e["event"] == "tool_result")
    assert tresult["ok"] is True
    assert pipeline.candidate_ns == []
    assert pipeline.run_count == 1


# --- Blueprint rollback policy + replan (D3) -----------------------


class SequencedPipeline:
    """A pipeline whose ``run`` returns a scripted ok/fail result per call.

    Drives a planned (Blueprint) turn deterministically: an early step succeeds
    (advancing the project's current version), then a later step fails so the
    per-step rollback policy can be observed. ``run`` also emits the real stage
    vocabulary so a failing attempt carries a ``last_stage`` for escalation.
    """

    def __init__(self, results: Sequence[tuple[bool, str | None]]) -> None:
        # Each entry is (ok, error-or-None). The last entry repeats if exhausted.
        self._results = list(results)
        self._i = 0
        self.run_count = 0

    def run(
        self,
        intent,
        export_dir=None,
        on_progress=None,
        prior_code=None,
        grounding=None,
        images=(),
        on_reading=None,
    ):
        ok, error = self._results[min(self._i, len(self._results) - 1)]
        self._i += 1
        self.run_count += 1
        if on_progress:
            on_progress(
                {
                    "event": "start",
                    "intent": intent,
                    "max_tries": 3,
                    "mode": "refine" if prior_code else "generate",
                }
            )
            on_progress(
                {
                    "event": "stage",
                    "phase": "build",
                    "status": "ok" if ok else "error",
                    "attempt": 1,
                }
            )
        glb = None
        if export_dir and ok:
            Path(export_dir).mkdir(parents=True, exist_ok=True)
            glb = str(Path(export_dir) / "model.glb")
            Path(glb).write_bytes(b"glTF\x00")
            (Path(export_dir) / "model.step").write_text("ISO")
        attempts = (
            []
            if ok
            else [
                Attempt(n=1, code="broken", stage="build", error=error),
            ]
        )
        return GenerationResult(
            ok=ok,
            intent=intent,
            code='params = {"size": 10}\nresult = None\n' if ok else None,
            error=error,
            volume=1000.0 if ok else None,
            bbox=(10, 10, 10) if ok else None,
            glb_path=glb,
            step_path=str(Path(export_dir) / "model.step") if (export_dir and ok) else None,
            parameters={"size": 10} if ok else {},
            attempts=attempts,
        )


def test_planned_turn_failed_step_reverts_to_last_ok_and_orchestrator_continues(
    client, store, monkeypatch
):
    """D3: in a planned (Blueprint) turn, a step that succeeds then a step that
    fails must auto-revert the project's current version to the last OK one (so a
    bad step never leaves the project on broken/partial geometry), feed the error
    back to the orchestrator, and let it keep going — the turn is NOT collapsed."""
    provider = ScriptedProvider(
        [
            _plan_turn(tool_use_id="tu-0", steps=["base", "feature"]),
            # Step 1 succeeds -> advances current to a new ok version.
            _tool_turn(tool_use_id="tu-1", name="edit_model", tool_input={"change": "base plate"}),
            # Step 2 fails -> per-step rollback must revert current to step 1's version.
            _tool_turn(
                tool_use_id="tu-2", name="edit_model", tool_input={"change": "broken feature"}
            ),
            # Orchestrator continues (replans/responds) rather than the turn collapsing.
            _text_turn("Reverted the bad step; let me try a different approach."),
        ]
    )
    pipeline = SequencedPipeline([(True, None), (False, "execution: boom")])
    _install(monkeypatch, provider, pipeline=pipeline)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    # Observe rollbacks AS THEY HAPPEN: the per-step policy reverts to last-ok the
    # moment a planned step fails (mid-loop), distinct from the whole-turn
    # settlement revert that also fires at the end of a failed turn.
    reverts: list[int | None] = []
    real_revert = chat._revert_to_last_ok

    async def _spy(store_, project_id):
        result = await real_revert(store_, project_id)
        reverts.append(result)
        return result

    monkeypatch.setattr(chat, "_revert_to_last_ok", _spy)

    events = _stream_chat(client, pid, "make a bracket")
    tool_results = [e for e in events if e["event"] == "tool_result"]
    assert len(tool_results) == 2
    ok_vid = tool_results[0]["version_id"]
    assert ok_vid is not None
    assert tool_results[0]["ok"] is True
    assert tool_results[1]["ok"] is False

    # The failed step triggered a PER-STEP revert (mid-loop) AND the whole-turn
    # settlement revert fires too -> two reverts, both targeting the last-ok vid.
    assert len(reverts) == 2
    assert reverts[0] == ok_vid

    # The orchestrator kept going AFTER the failed step (turn not collapsed).
    kinds = [e["event"] for e in events]
    assert any(e["event"] == "text_delta" and "Reverted" in e["text"] for e in events)
    assert kinds[-1] == "turn_end"
    assert pipeline.run_count == 2  # both steps actually executed

    # Per-step rollback: project's current is back at the last OK version (step 1),
    # NOT left on the failed step's partial geometry.
    assert client.get(f"/projects/{pid}").json()["current_version_id"] == ok_vid


def test_planned_turn_repeated_failures_are_bounded_by_escalation(client, store, monkeypatch):
    """D3: the retry/replan allowed by the rollback policy is bounded — repeated
    failures at the same stage trigger the existing escalation, steering
    the orchestrator off the cycle so it can't loop forever."""
    provider = ScriptedProvider(
        [
            _plan_turn(tool_use_id="tu-0", steps=["base", "feature"]),
            _tool_turn(tool_use_id="tu-1", name="edit_model", tool_input={"change": "try A"}),
            _tool_turn(tool_use_id="tu-2", name="edit_model", tool_input={"change": "try B"}),
            _tool_turn(tool_use_id="tu-3", name="edit_model", tool_input={"change": "try C"}),
            _text_turn("Giving up on this approach."),
        ]
    )
    # Every step fails at the same 'build' stage.
    pipeline = SequencedPipeline([(False, "execution: boom")])
    _install(monkeypatch, provider, pipeline=pipeline)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    _stream_chat(client, pid, "make a bracket")

    # The escalation hint (steer toward ask_clarification) was appended to a failed
    # tool_result the orchestrator saw — bounding the cycle (default threshold 2).
    msgs = _messages(store, pid)
    tool_result_texts = [b.content or "" for b in msgs[1].blocks if b.kind == "tool_result"]
    assert any("ask_clarification" in t for t in tool_result_texts)


def test_unplanned_failed_step_does_not_per_step_revert(client, store, monkeypatch):
    """D3 gating: an ordinary (no plan) turn keeps today's behavior — a failed step
    does NOT trigger the Blueprint per-step revert. Whole-turn settlement still
    guarantees current == last-ok at the end, but no extra per-step rollback runs
    mid-turn (no plan is active)."""
    provider = ScriptedProvider(
        [
            _tool_turn(tool_use_id="tu-1", name="edit_model", tool_input={"change": "base"}),
            _tool_turn(tool_use_id="tu-2", name="edit_model", tool_input={"change": "broken"}),
            _text_turn("Sorry, that failed."),
        ]
    )
    pipeline = SequencedPipeline([(True, None), (False, "execution: boom")])
    _install(monkeypatch, provider, pipeline=pipeline)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    reverts: list[int] = []
    real_revert = chat._revert_to_last_ok

    async def _spy(store_, project_id):
        result = await real_revert(store_, project_id)
        reverts.append(result if result is not None else -1)
        return result

    monkeypatch.setattr(chat, "_revert_to_last_ok", _spy)

    events = _stream_chat(client, pid, "make a bracket")
    tool_results = [e for e in events if e["event"] == "tool_result"]
    ok_vid = tool_results[0]["version_id"]

    # Whole-turn settlement reverts to last-ok ONCE (today's behavior), not an extra
    # per-step revert during the loop.
    assert len(reverts) == 1
    # And the final current is the last-ok version (the successful first step).
    assert client.get(f"/projects/{pid}").json()["current_version_id"] == ok_vid


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


# --- attached reference images ----------------------------------------------


def _image(*, size: int = 32, media_type: str = "image/png") -> dict:
    """One attachment payload of ``size`` decoded bytes."""
    return {"media_type": media_type, "data": base64.b64encode(b"\x89PNG" * size).decode()}


def _chat_with_images(client, pid, images, text="build this"):
    """POST a turn carrying attachments and return its decoded SSE events."""
    with client.stream(
        "POST", f"/projects/{pid}/chat", json={"message": text, "images": images}
    ) as r:
        assert r.status_code == 200
        return _events("".join(r.iter_text()))


def _errors(events: list[dict]) -> list[str]:
    return [e.get("detail", "") for e in events if e.get("event") == "error"]


def _current_version_id(store, pid):
    async def _go():
        return (await store.get_project(pid)).current_version_id

    return asyncio.run(_go())


class _BlindProvider(ScriptedProvider):
    """A provider whose model cannot read an image."""

    def capabilities(self, model: str):
        caps = super().capabilities(model)
        return caps.model_copy(update={"supports_images": False})


def test_chat_refuses_an_image_over_the_per_image_limit(client, store, monkeypatch):
    _install(monkeypatch, ScriptedProvider([_text_turn("should not be reached")]))
    monkeypatch.setattr(chat.settings, "chat_image_max_bytes", 64)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    errors = _errors(_chat_with_images(client, pid, [_image(size=64)]))

    assert len(errors) == 1
    # The refusal has to say which limit was hit, not merely that one was.
    assert "size" in errors[0].lower() or "large" in errors[0].lower()
    assert "64" in errors[0]


def test_an_image_at_the_limit_is_not_refused_by_the_encoded_pre_check(client, store, monkeypatch):
    # The pre-check refuses on the base64 length so an oversize payload is not
    # decoded before being told no. Its bound therefore has to be loose enough that
    # a picture exactly at the ceiling still gets through — a pre-check that
    # over-refuses would reject valid images and no size test would notice.
    _install(monkeypatch, ScriptedProvider([_text_turn("ok")]))
    monkeypatch.setattr(chat.settings, "chat_image_max_bytes", 1024)
    monkeypatch.setattr(chat.settings, "chat_image_max_turn_bytes", 4096)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    exactly_at_limit = {
        "media_type": "image/png",
        "data": base64.b64encode(b"x" * 1024).decode(),
    }
    assert _errors(_chat_with_images(client, pid, [exactly_at_limit])) == []


def test_the_encoded_ceiling_never_refuses_a_payload_within_the_decoded_limit():
    # Checked across the three base64 padding cases, since the encoded length of a
    # given byte count is not a single formula but rounds up to a multiple of four.
    for size in range(0, 200):
        encoded = len(base64.b64encode(b"x" * size))
        assert encoded <= chat._encoded_ceiling(size)


def test_chat_refuses_more_images_than_the_count_limit(client, store, monkeypatch):
    _install(monkeypatch, ScriptedProvider([_text_turn("should not be reached")]))
    monkeypatch.setattr(chat.settings, "chat_image_max_count", 2)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    errors = _errors(_chat_with_images(client, pid, [_image(), _image(), _image()]))

    assert len(errors) == 1
    assert "2" in errors[0]
    assert "3" in errors[0]  # says how many arrived, so the user can act on it


def test_chat_refuses_images_over_the_whole_turn_limit(client, store, monkeypatch):
    _install(monkeypatch, ScriptedProvider([_text_turn("should not be reached")]))
    monkeypatch.setattr(chat.settings, "chat_image_max_bytes", 1_000_000)
    monkeypatch.setattr(chat.settings, "chat_image_max_turn_bytes", 300)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    # Each image is under the per-image ceiling; together they are over the turn's.
    errors = _errors(_chat_with_images(client, pid, [_image(size=50), _image(size=50)]))

    assert len(errors) == 1
    assert "300" in errors[0]


def test_chat_refuses_an_unsupported_media_type(client, store, monkeypatch):
    _install(monkeypatch, ScriptedProvider([_text_turn("should not be reached")]))
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    errors = _errors(_chat_with_images(client, pid, [_image(media_type="image/tiff")]))

    assert len(errors) == 1
    assert "image/tiff" in errors[0]  # names the format that was refused


def test_chat_refuses_an_image_when_the_model_cannot_see(client, store, monkeypatch):
    _install(monkeypatch, _BlindProvider([_text_turn("should not be reached")]))
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    errors = _errors(_chat_with_images(client, pid, [_image()]))

    assert len(errors) == 1
    assert "image" in errors[0].lower()


def test_a_refused_attachment_leaves_the_project_version_alone(client, store, monkeypatch):
    # An error raised *inside* a turn reverts the project to its last good version.
    # A refused attachment must not: nothing was built, so nothing is rolled back.
    provider = ScriptedProvider(
        [
            _tool_turn(tool_use_id="tu-1", name="generate_model", tool_input={"spec": "a cube"}),
            _text_turn("Done."),
        ]
    )
    _install(monkeypatch, provider)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]
    _stream_chat(client, pid)
    before = _current_version_id(store, pid)
    assert before is not None  # the turn really did produce a version

    monkeypatch.setattr(chat.settings, "chat_image_max_count", 1)
    _chat_with_images(client, pid, [_image(), _image()])

    assert _current_version_id(store, pid) == before


def test_chat_accepts_a_turn_that_is_only_an_image(client, store, monkeypatch):
    # "here, build this" with the picture doing the talking. The message field is
    # empty, which the request model used to reject outright.
    _install(monkeypatch, ScriptedProvider([_text_turn("A bracket, then.")]))
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    events = _chat_with_images(client, pid, [_image()], text="")

    assert _errors(events) == []


def test_chat_still_refuses_a_turn_with_neither_text_nor_image(client, store, monkeypatch):
    _install(monkeypatch, ScriptedProvider([_text_turn("should not be reached")]))
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    r = client.post(f"/projects/{pid}/chat", json={"message": "", "images": []})

    assert r.status_code == 422


def test_steer_refuses_an_attached_image(client, store, monkeypatch):
    # Steer forwards a bare string into the running loop, so an image on this
    # route would be accepted and then silently dropped. Refuse it instead.
    _install(monkeypatch, ScriptedProvider([_text_turn("ok")]))
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    r = client.post(
        f"/projects/{pid}/chat/steer",
        json={"message": "actually make it taller", "images": [_image()]},
    )

    assert r.status_code == 422


def _replay(store, pid, blocks, content="", role="user"):
    async def go():
        sess = await store.get_or_create_session(pid)
        await store.add_message(sess.id, role, content, blocks=blocks)
        return await chat._replay_history(store, sess.id)

    return asyncio.run(go())


def test_replay_carries_the_reading_rather_than_the_bytes(client, store):
    pid = client.post("/projects", json={"name": "P"}).json()["id"]
    block = ContentBlock.of_image(
        data="aGVsbG8=", media_type="image/png", reading="an L-bracket with two holes"
    )

    msgs = _replay(store, pid, [block, ContentBlock.of_text("build this")], content="build this")

    assert {b.kind for m in msgs for b in m.content} == {"text"}  # no pixels replayed
    replayed = msgs[0].content[0].text
    assert "an L-bracket with two holes" in replayed
    assert "build this" in replayed
    assert "aGVsbG8=" not in replayed


def test_replay_falls_back_to_a_placeholder_with_no_reading(client, store):
    pid = client.post("/projects", json={"name": "P"}).json()["id"]
    block = ContentBlock.of_image(data="aGVsbG8=", media_type="image/png")

    msgs = _replay(store, pid, [block, ContentBlock.of_text("build this")], content="build this")

    assert "image" in msgs[0].content[0].text.lower()


def test_an_image_only_turn_is_not_dropped_from_the_replayed_history(client, store):
    # The whole message used to vanish, not just the image: an image-only turn
    # flattened to an empty string and the empty check skipped it entirely, so the
    # next turn's model never learned the conversation had a picture in it.
    pid = client.post("/projects", json={"name": "P"}).json()["id"]
    block = ContentBlock.of_image(data="aGVsbG8=", media_type="image/png", reading="a cube")

    msgs = _replay(store, pid, [block], content="")

    assert len(msgs) == 1
    assert "a cube" in msgs[0].content[0].text


def test_a_second_turn_sends_the_reading_and_never_the_picture(client, store, monkeypatch):
    provider = ScriptedProvider([_text_turn("A bracket."), _text_turn("Taller, then.")])
    _install(monkeypatch, provider)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    _chat_with_images(client, pid, [_image()], text="build this")
    _stream_chat(client, pid, "now make it twice as tall")

    replayed = provider.calls[-1]["messages"]
    assert "image" not in [b.kind for m in replayed for b in m.content]


def test_an_attached_image_reaches_the_orchestrator(client, store, monkeypatch):
    provider = ScriptedProvider([_text_turn("A bracket.")])
    _install(monkeypatch, provider)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    _chat_with_images(client, pid, [_image()], text="build this")

    sent = provider.calls[-1]["messages"][-1].content
    assert "image" in [b.kind for b in sent]


def test_an_attached_image_reaches_the_model_that_writes_the_script(client, store, monkeypatch):
    # The orchestrator seeing it is not enough — the acceptance criterion is that
    # the picture reaches the codegen call, which only happens if the tool layer
    # forwards it into the pipeline.
    provider = ScriptedProvider(
        [
            _tool_turn(tool_use_id="tu-1", name="generate_model", tool_input={"spec": "a bracket"}),
            _text_turn("Done."),
        ]
    )
    pipeline = StubPipeline()
    _install(monkeypatch, provider, pipeline=pipeline)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    _chat_with_images(client, pid, [_image()], text="build this")

    assert pipeline.images  # the tool actually ran
    assert [b.kind for b in pipeline.images[-1]] == ["image"]


def test_an_edit_turn_carries_the_image_into_the_pipeline_too(client, store, monkeypatch):
    provider = ScriptedProvider(
        [
            _tool_turn(tool_use_id="tu-1", name="edit_model", tool_input={"change": "taller"}),
            _text_turn("Done."),
        ]
    )
    pipeline = StubPipeline()
    _install(monkeypatch, provider, pipeline=pipeline)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    _chat_with_images(client, pid, [_image()], text="like this, but taller")

    assert [b.kind for b in pipeline.images[-1]] == ["image"]


def test_the_words_are_kept_beside_the_picture_in_the_transcript(client, store, monkeypatch):
    # Persisting blocks stops MessageOut synthesizing one from ``content``, so the
    # message text has to be carried explicitly or it vanishes from the UI.
    _install(monkeypatch, ScriptedProvider([_text_turn("ok")]))
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    _chat_with_images(client, pid, [_image()], text="build this")

    user = _messages(store, pid)[0]
    assert [b.kind for b in user.blocks] == ["image", "text"]
    assert user.blocks[1].text == "build this"


def test_the_reading_is_stored_beside_the_picture_it_describes(client, store, monkeypatch):
    provider = ScriptedProvider(
        [
            _tool_turn(tool_use_id="tu-1", name="generate_model", tool_input={"spec": "a bracket"}),
            _text_turn("Done."),
        ]
    )
    pipeline = StubPipeline()
    pipeline.reading = "an L-bracket with two bolt holes"
    _install(monkeypatch, provider, pipeline=pipeline)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    _chat_with_images(client, pid, [_image()], text="build this")

    image_block = _messages(store, pid)[0].blocks[0]
    assert image_block.kind == "image"
    assert image_block.reading == "an L-bracket with two bolt holes"


def test_a_turn_whose_model_wrote_no_reading_still_settles(client, store, monkeypatch):
    # The cache is a convenience. A model that ignored the instruction must leave a
    # working turn behind, with the image simply carrying no reading.
    provider = ScriptedProvider(
        [
            _tool_turn(tool_use_id="tu-1", name="generate_model", tool_input={"spec": "a bracket"}),
            _text_turn("Done."),
        ]
    )
    pipeline = StubPipeline()  # reading stays None
    _install(monkeypatch, provider, pipeline=pipeline)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    events = _chat_with_images(client, pid, [_image()], text="build this")

    assert _errors(events) == []
    assert _messages(store, pid)[0].blocks[0].reading is None


def test_a_text_only_turn_persists_exactly_as_before(client, store, monkeypatch):
    _install(monkeypatch, ScriptedProvider([_text_turn("ok")]))
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    _stream_chat(client, pid, "make a cube")

    user = _messages(store, pid)[0]
    assert user.blocks == []  # no synthesized blocks; content still carries it
    assert user.content == "make a cube"


def test_steer_still_accepts_a_plain_message(client, store, monkeypatch):
    _install(monkeypatch, ScriptedProvider([_text_turn("ok")]))
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    r = client.post(f"/projects/{pid}/chat/steer", json={"message": "make it taller"})

    assert r.status_code == 202


def test_steer_still_accepts_the_body_it_always_did(client, store, monkeypatch):
    # This route shared ChatRequest, so a client sending `forge` was accepted and
    # the value ignored. Narrowing the model to refuse images must not turn that
    # into a 422 as a side effect — it is a public HTTP contract.
    _install(monkeypatch, ScriptedProvider([_text_turn("ok")]))
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    r = client.post(
        f"/projects/{pid}/chat/steer", json={"message": "make it taller", "forge": False}
    )

    assert r.status_code == 202


# --- the render critique's captures -----------------------------------------

_CRITIQUE_VIEWS = ("front", "right", "top", "iso")


class _CritiquingPipeline(StubPipeline):
    """Reports two critique rounds after building, as the real loop does."""

    def run(
        self,
        intent,
        export_dir=None,
        on_progress=None,
        prior_code=None,
        grounding=None,
        images=(),
        on_reading=None,
    ):
        result = super().run(
            intent,
            export_dir=export_dir,
            on_progress=on_progress,
            prior_code=prior_code,
            grounding=grounding,
            images=images,
            on_reading=on_reading,
        )
        for attempt, (matches, feedback) in enumerate(((False, "too tall"), (True, "")), start=1):
            on_progress(
                {
                    "event": "critique",
                    "attempt": attempt,
                    "matches": matches,
                    "feedback": feedback,
                    "views": [
                        {
                            "name": name,
                            "png_b64": base64.standard_b64encode(
                                f"PNG:{name}:{attempt}".encode()
                            ).decode("ascii"),
                        }
                        for name in _CRITIQUE_VIEWS
                    ],
                }
            )
        return result


def _generate_turn(provider_text="Done."):
    return [
        _tool_turn(tool_use_id="tu-1", name="generate_model", tool_input={"spec": "a cube"}),
        _text_turn(provider_text),
    ]


def test_every_critique_round_streams_live(client, store, monkeypatch):
    """Both rounds reach the client, and not inside the post-tool burst.

    Collected progress events arrive as one ``tool_progress`` once the tool has
    settled — after the loop these describe is over — so a capture delivered
    that way is no longer news. Asserting the absence from that burst is what
    keeps them on the live channel.
    """
    _install(monkeypatch, ScriptedProvider(_generate_turn()), pipeline=_CritiquingPipeline())
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    events = _stream_chat(client, pid)

    rounds = [e for e in events if e["event"] == "critique"]
    assert [r["attempt"] for r in rounds] == [1, 2]
    assert [r["matches"] for r in rounds] == [False, True]
    assert rounds[0]["feedback"] == "too tall"
    assert [v["name"] for v in rounds[0]["views"]] == list(_CRITIQUE_VIEWS)
    assert base64.standard_b64decode(rounds[0]["views"][0]["png_b64"]) == b"PNG:front:1"

    nested = [e["stage"] for e in events if e["event"] == "tool_progress" and "stage" in e]
    assert not [s for s in nested if s.get("event") == "critique"], "delivered twice"


def test_the_last_round_of_captures_survives_a_reload(client, store, monkeypatch):
    """The transcript keeps the round the turn ended on, not every round.

    The stream is what shows the work as it happens; a reload wants the state
    the turn finished in. Keeping every round would multiply a turn's stored
    bytes by however many it took to settle.
    """
    _install(monkeypatch, ScriptedProvider(_generate_turn()), pipeline=_CritiquingPipeline())
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    _stream_chat(client, pid)

    assistant = [m for m in _messages(store, pid) if m.role == "assistant"][-1]
    captures = [b for b in assistant.blocks if b.kind == "image"]
    assert len(captures) == len(_CRITIQUE_VIEWS)
    assert base64.standard_b64decode(captures[0].data) == b"PNG:front:2"  # the last round
    assert [c.reading for c in captures] == [
        f"a render of the part this turn built, seen from the {name}" for name in _CRITIQUE_VIEWS
    ]

    # The transcript the browser reads carries the shape without the bytes, and
    # the attachment route — untouched, and role-agnostic — serves each one.
    out = client.get(f"/projects/{pid}/messages").json()
    served = next(m for m in out if m["role"] == "assistant" and m["blocks"])
    assert [b["data"] for b in served["blocks"] if b["kind"] == "image"] == [None] * 4
    r = client.get(f"/projects/{pid}/messages/{assistant.id}/attachments/0")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/png")
    assert r.content == b"PNG:front:2"


def test_an_edit_turn_routes_its_critique_the_same_way(client, store, monkeypatch):
    """An edit can build the wrong shape just as readily as a fresh generation.

    Left on the raw callback the captures ride inside the collected burst —
    arriving after the loop they describe, and never reaching the transcript —
    so a mismatch that forced a repair on an edit would leave no trace at all
    while still being paid for.
    """
    provider = ScriptedProvider(
        [
            _tool_turn(tool_use_id="tu-1", name="edit_model", tool_input={"change": "taller"}),
            _text_turn("Done."),
        ]
    )
    _install(monkeypatch, provider, pipeline=_CritiquingPipeline())
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    events = _stream_chat(client, pid, "make it taller")

    assert [e["attempt"] for e in events if e["event"] == "critique"] == [1, 2]
    nested = [e["stage"] for e in events if e["event"] == "tool_progress" and "stage" in e]
    assert not [s for s in nested if s.get("event") == "critique"], "rode the collected burst"

    assistant = [m for m in _messages(store, pid) if m.role == "assistant"][-1]
    assert len([b for b in assistant.blocks if b.kind == "image"]) == len(_CRITIQUE_VIEWS)


def test_the_verdict_is_stored_beside_the_captures(client, store, monkeypatch):
    """Four renders with nothing said about them ask the reader to guess.

    An image on an assistant turn replays as nothing and carries no verdict of
    its own, so the sentence has to be its own block for a reload to be worth
    anything — and, unlike the pictures, it is worth replaying.
    """
    _install(monkeypatch, ScriptedProvider(_generate_turn()), pipeline=_CritiquingPipeline())
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    _stream_chat(client, pid)

    assistant = [m for m in _messages(store, pid) if m.role == "assistant"][-1]
    said = [b.text for b in assistant.blocks if b.kind == "text"]
    assert any(
        t == "Reviewed the build from front, right, top, iso: it matches the request." for t in said
    ), said


def test_a_mismatch_verdict_says_what_was_wrong(client, store, monkeypatch):
    class _OneBadRound(_CritiquingPipeline):
        def run(self, intent, export_dir=None, on_progress=None, **kw):
            result = StubPipeline.run(self, intent, export_dir=export_dir, on_progress=on_progress)
            on_progress(
                {
                    "event": "critique",
                    "attempt": 1,
                    "matches": False,
                    "feedback": "the bore is too small",
                    "views": [{"name": "front", "png_b64": "eA=="}],
                }
            )
            return result

    _install(monkeypatch, ScriptedProvider(_generate_turn()), pipeline=_OneBadRound())
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    _stream_chat(client, pid)

    assistant = [m for m in _messages(store, pid) if m.role == "assistant"][-1]
    said = [b.text for b in assistant.blocks if b.kind == "text"]
    assert "Reviewed the build from front: the bore is too small." in said, said


def test_nothing_the_review_stored_is_replayed_into_a_later_turn(client, store, monkeypatch):
    """Neither the renders nor the sentence was something the model said.

    The images replayed through the reference-image path would misdescribe
    where they came from and add four lines to every turn that follows, for the
    rest of the session. The sentence is worse: it is free prose a vision model
    wrote from a prompt carrying the user's own words, and replaying it hands
    the orchestrator that text as a fact the assistant had itself established.
    Both are kept for a reader reloading the page and for nobody else.
    """
    provider = ScriptedProvider(_generate_turn() + [_text_turn("And again.")])
    _install(monkeypatch, provider, pipeline=_CritiquingPipeline())
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    _stream_chat(client, pid)
    _stream_chat(client, pid, "and again")

    replayed = "\n".join(
        block.text or ""
        for call in provider.calls
        for message in call["messages"]
        for block in message.content
    )
    assert "reference image" not in replayed
    assert "Reviewed the build from" not in replayed
    # The model's own words still replay — this must narrow what reaches the
    # orchestrator, not silence the assistant turn it is attached to.
    assert "Done." in replayed
