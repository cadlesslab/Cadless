"""The scripted provider, stub pipelines and stream helpers the chat API tests share.

Turn builders script what the provider streams, ``_install`` wires a provider and a
pipeline into the chat router, and the readers decode its SSE stream or read back what
the store kept. It holds no fixtures: a fixture imported by name and then named as a
parameter reads to a linter as a redefinition.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from pathlib import Path

import backend.routers.chat as chat
from cadless.config import settings
from cadless.llm.providers import StreamChunk
from cadless.llm.providers.fake import FakeChatProvider
from cadless.llm.types import ContentBlock, StreamEvent
from cadless.pipeline import Attempt, GenerationResult

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

    def __init__(
        self, *, ok: bool = True, error: str | None = None, assembly: dict | None = None
    ) -> None:
        self.ok = ok
        self.error = error
        # The assembly check's verdict on the build, as the real pipeline keeps it.
        self.verdict = assembly
        self.groundings: list[str | None] = []
        self.images: list[list] = []
        # What each turn asked for by way of an assembly, recorded the same way
        # groundings are: the per-turn block is the thing under test, and the
        # route is the only place the both-true gate is applied.
        self.assemblies: list = []
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
        assembly=None,
    ):
        self.groundings.append(grounding)
        self.images.append(list(images))
        self.assemblies.append(assembly)
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
            # A refused assembly's last attempt failed at the check, as the real
            # repair loop records it.
            attempts=[Attempt(n=1, code="", stage="assembly", error="refused")]
            if self.verdict is not None and self.verdict.get("ok") is False
            else [],
            assembly=self.verdict,
        )


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


def _kb_entries(store):
    import asyncio

    return asyncio.run(store.list_kb_entries())


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
        assembly=None,
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
        assembly=None,
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


def _stream_assembly(client, pid, *, assembly: bool, text="make a bookshelf"):
    body = {"message": text, "assembly": assembly}
    with client.stream("POST", f"/projects/{pid}/chat", json=body) as r:
        assert r.status_code == 200
        return _events("".join(r.iter_text()))


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
        assembly=None,
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
