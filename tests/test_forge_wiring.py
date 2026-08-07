"""Forge live-wiring at the agent layer (C4).

When a turn is forge-active (``ToolContext.forge`` + ``forge_n > 1``), a FRESH
``generate_model`` must go through the C1 (fan-out) -> C2 (judge) race instead of a
single ``pipeline.run``, returning the winner as the normal tool payload AND
surfacing the losing candidates so the chat layer can persist them as non-current
rows. ``edit_model`` / ``set_parameters`` NEVER forge. A forge-inactive turn (no
opt-in, or N<=1) is byte-for-byte the legacy single-run path.

Driven entirely offline: a fake pipeline records ``run`` vs ``run_candidates``.
"""

from __future__ import annotations

from cadless.agent import Agent, ToolContext
from cadless.llm.providers.fake import FakeChatProvider
from cadless.llm.types import ContentBlock
from cadless.pipeline import GenerationResult


class RacePipeline:
    """Records whether the agent called the single-run path or the fan-out race."""

    def __init__(self, candidates: list[GenerationResult]) -> None:
        self._candidates = candidates
        self.run_calls: list[tuple[str, str | None]] = []
        self.candidate_calls: list[int | None] = []

    def run(
        self,
        intent,
        export_dir=None,
        on_progress=None,
        prior_code=None,
        grounding=None,
        temperature=None,
    ):
        self.run_calls.append((intent, prior_code))
        return self._candidates[0]

    def run_candidates(
        self, intent, n=None, export_dir=None, assertions=None, grounding=None, temperature=None
    ):
        self.candidate_calls.append(n)
        return self._candidates


def _cand(code, *, ok=True, volume=None) -> GenerationResult:
    return GenerationResult(
        ok=ok, intent="a bracket", code=code, volume=volume, parameters={}, glb_path=None
    )


def _gen_block(spec="a bracket"):
    return ContentBlock.of_tool_use(id="tu-1", name="generate_model", input={"spec": spec})


def _agent():
    return Agent(provider=FakeChatProvider(), model="fake-model")


def test_forge_active_generate_uses_race_not_single_run():
    """Forge-active fresh generation fans out N candidates via run_candidates."""
    good = _cand("result = Box(2,2,2)", ok=True, volume=8.0)
    pipe = RacePipeline([good, _cand("bad1", ok=False), _cand("bad2", ok=False)])
    ctx = ToolContext(pipeline=pipe, forge=True, forge_n=3)

    block, payload = _agent()._execute_one(_gen_block(), ctx)

    assert pipe.candidate_calls == [3]  # raced
    assert pipe.run_calls == []  # NOT the single-run path
    assert payload["ok"] is True
    assert payload["code"] == "result = Box(2,2,2)"


def test_forge_active_surfaces_losers_for_persistence():
    """The winner payload carries the losing candidates so the chat layer can
    persist them as non-current rows."""
    good = _cand("result = Box(2,2,2)", ok=True, volume=8.0)
    bad1 = _cand("bad1", ok=False)
    bad2 = _cand("bad2", ok=False)
    pipe = RacePipeline([good, bad1, bad2])
    ctx = ToolContext(pipeline=pipe, forge=True, forge_n=3)

    _, payload = _agent()._execute_one(_gen_block(), ctx)

    forge = payload.get("forge")
    assert forge is not None
    loser_codes = {lc.code for lc in forge["losers"]}
    assert loser_codes == {"bad1", "bad2"}


def test_forge_inactive_generate_uses_single_run():
    """No opt-in => the legacy single-run path; no race, no forge payload."""
    pipe = RacePipeline([_cand("result = Box(1,1,1)", ok=True, volume=1.0)])
    ctx = ToolContext(pipeline=pipe, forge=False, forge_n=3)

    _, payload = _agent()._execute_one(_gen_block(), ctx)

    assert pipe.run_calls == [("a bracket", None)]
    assert pipe.candidate_calls == []
    assert payload.get("forge") is None


def test_forge_active_but_n_one_uses_single_run():
    """forge_n<=1 is not a race (budget bought one sample): legacy single-run."""
    pipe = RacePipeline([_cand("result = Box(1,1,1)", ok=True, volume=1.0)])
    ctx = ToolContext(pipeline=pipe, forge=True, forge_n=1)

    _, payload = _agent()._execute_one(_gen_block(), ctx)

    assert pipe.run_calls == [("a bracket", None)]
    assert pipe.candidate_calls == []
    assert payload.get("forge") is None


def test_forge_never_applies_to_edit_model():
    """edit_model is low-variance: it must always take the single-run path."""
    pipe = RacePipeline([_cand("result = Box(1,1,1)", ok=True, volume=1.0)])
    ctx = ToolContext(pipeline=pipe, forge=True, forge_n=3, current_code="result = Box(1,1,1)")
    edit = ContentBlock.of_tool_use(
        id="tu-2", name="edit_model", input={"change": "make it bigger"}
    )

    _, payload = _agent()._execute_one(edit, ctx)

    assert pipe.candidate_calls == []  # never raced
    assert pipe.run_calls == [("make it bigger", "result = Box(1,1,1)")]
    assert payload.get("forge") is None


def test_forge_never_applies_to_set_parameters():
    """set_parameters is deterministic: no race, ever."""
    pipe = RacePipeline([_cand("result = Box(1,1,1)", ok=True)])
    recorded = {}

    def reparam(code, overrides):
        recorded["called"] = (code, overrides)
        return {"ok": True, "code": code, "parameters": overrides}

    ctx = ToolContext(
        pipeline=pipe,
        forge=True,
        forge_n=3,
        current_code="result = Box(1,1,1)",
        reparametrize=reparam,
    )
    setp = ContentBlock.of_tool_use(id="tu-3", name="set_parameters", input={"params": {"size": 5}})

    _, payload = _agent()._execute_one(setp, ctx)

    assert pipe.candidate_calls == []
    assert recorded["called"] == ("result = Box(1,1,1)", {"size": 5})
    assert payload.get("forge") is None
