"""The shared race-and-judge primitive (C1 fan-out composed with C2 selection).

Both the live agent turn and the evaluation harness go through this one function,
which is what stops an A/B of forge from measuring a reimplementation instead of
the shipped behaviour. These tests drive it directly, with no agent and no chat
layer: a stub pipeline records what the fan-out was asked for, and stub judging
dependencies decide which rung gets to answer.

Fully offline — no provider, no network, no OCCT.
"""

from __future__ import annotations

from dataclasses import dataclass

from cadless.forge import race_and_judge
from cadless.judge import Rung
from cadless.pipeline import Attempt, GenerationResult


class StubPipeline:
    """Returns a fixed candidate field and records how it was asked for."""

    def __init__(self, candidates: list[GenerationResult]) -> None:
        self._candidates = candidates
        self.calls: list[dict] = []

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
        self.calls.append(
            {
                "intent": intent,
                "n": n,
                "export_dir": export_dir,
                "assertions": assertions,
                "grounding": grounding,
                "images": images,
                "on_reading": on_reading,
            }
        )
        return self._candidates


class ScoringProvider:
    """Scores a candidate by looking for a known fragment of its code."""

    def __init__(self, scores: dict[str, int]) -> None:
        self._scores = scores
        self.calls = 0

    def complete(self, *, model, system, user, temperature=None) -> str:
        self.calls += 1
        for fragment, score in self._scores.items():
            if fragment in user:
                return str(score)
        return "0"


@dataclass
class _Critique:
    matches: bool


class StubCritic:
    """Approves exactly the render paths it was told to approve."""

    def __init__(self, matching: set[str]) -> None:
        self._matching = matching
        self.calls: list[str] = []

    def critique(self, intent, mesh_path):
        self.calls.append(mesh_path)
        return _Critique(matches=mesh_path in self._matching)


def _cand(code, *, ok=True, attempts=1, glb_path=None, stl_path=None) -> GenerationResult:
    return GenerationResult(
        ok=ok,
        intent="a bracket",
        code=code,
        glb_path=glb_path,
        stl_path=stl_path,
        attempts=[
            Attempt(n=i + 1, code=code, stage="execute", error=None) for i in range(attempts)
        ],
    )


def test_race_forwards_the_fan_out_arguments():
    """Everything the caller supplies for generation reaches ``run_candidates``."""
    pipe = StubPipeline([_cand("a")])
    reader = object()

    race_and_judge(
        pipe,
        "a bracket",
        n=4,
        export_dir="/tmp/out",
        grounding="some grounding",
        images=("img",),
        on_reading=reader,
    )

    assert pipe.calls == [
        {
            "intent": "a bracket",
            "n": 4,
            "export_dir": "/tmp/out",
            "assertions": None,
            "grounding": "some grounding",
            "images": ("img",),
            "on_reading": reader,
        }
    ]


def test_race_returns_the_whole_field_beside_the_verdict():
    """Losers come back too: the live path persists them and the eval prices them."""
    field = [_cand("a"), _cand("b"), _cand("c")]
    pipe = StubPipeline(field)

    judged, candidates = race_and_judge(pipe, "a bracket", n=3)

    assert candidates == field
    assert judged.winner in field


def test_provider_reaches_the_cheap_llm_rung():
    """With a provider the LLM rung decides, rather than input order."""
    first, better = _cand("result = Box(1,1,1)"), _cand("result = Box(2,2,2)")
    provider = ScoringProvider({"Box(1,1,1)": 1, "Box(2,2,2)": 8})

    judged, _ = race_and_judge(StubPipeline([first, better]), "a bracket", n=2, provider=provider)

    assert provider.calls == 2
    assert judged.rung is Rung.LLM
    assert judged.winner is better


def test_critic_reaches_the_render_rung():
    """With a critic the render rung decides before the LLM rung is reached.

    The rung is handed the STL, not the GLB: the critic renders tessellated
    triangles and has no GLB loader, so a candidate that exported only a GLB is
    not judgeable here at all.
    """
    first = _cand("result = Box(1,1,1)", stl_path="/r/first.stl")
    better = _cand("result = Box(2,2,2)", stl_path="/r/better.stl")
    critic = StubCritic(matching={"/r/better.stl"})

    judged, _ = race_and_judge(StubPipeline([first, better]), "a bracket", n=2, critic=critic)

    assert critic.calls == ["/r/first.stl", "/r/better.stl"]
    assert judged.rung is Rung.VLM
    assert judged.winner is better


def test_without_judging_dependencies_the_ladder_falls_through_to_input_order():
    """The degraded mode, pinned on purpose.

    This is what a race costs when nothing is injected: the hard filter is the only
    live rung, so the first candidate that built wins and the rest were paid for and
    discarded. It is recorded here so that a future change which silently drops a
    judging dependency shows up as this test's ``rung`` flipping back to FILTER.
    """
    first, better = _cand("result = Box(1,1,1)"), _cand("result = Box(2,2,2)")

    judged, _ = race_and_judge(StubPipeline([first, better]), "a bracket", n=2)

    assert judged.rung is Rung.FILTER
    assert judged.decided is False  # no rung chose; input order did
    assert judged.winner is first


def test_a_race_nobody_wins_is_flagged_rather_than_promoted():
    """No candidate built => ``no_winner``, so a failed race never replaces a model."""
    failed = [_cand("a", ok=False, attempts=3), _cand("b", ok=False, attempts=2)]

    judged, candidates = race_and_judge(StubPipeline(failed), "a bracket", n=2)

    assert judged.no_winner is True
    assert judged.winner is failed[1]  # least-bad: got furthest on fewest attempts
    assert candidates == failed
