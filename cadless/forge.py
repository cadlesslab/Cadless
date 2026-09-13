"""Forge mode: run a best-of-N race, and persist the candidates that lose it (C4).

Pillar 4 racing — *branch, evaluate, keep the winner, discard the rest*. C1
(``Pipeline.run_candidates``) fans out N fresh candidates and C2 (``judge.select_winner``)
picks the surviving line. :func:`race_and_judge` is that composition, and
:func:`persist_losers` is the persistence leg: the winner is written by the agent's
normal tool-version path (set as current, artifacts copied), while the remaining
candidates become NON-CURRENT ``script_versions`` rows.

Each loser shares the winner's ``parent_version_id`` (a parallel checkpoint) and is
flagged with ``candidate_of_version_id`` pointing at the winning sibling, so the race
stays retrievable (via ``Store.list_candidate_versions`` / the ``/versions/{id}/candidates``
endpoint) yet a loser is never the current version. No new table — racing reuses the
parent pointer + the existing current-version flag (``projects.current_version_id``).

The store types are imported for annotations only, so that measuring forge does not
drag the async persistence layer in behind it: the evaluation harness needs the race
and never needs a store.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from cadless.assertions import GeometryAssertions
from cadless.judge import JudgeResult, SignatureOf, select_winner
from cadless.pipeline import GenerationResult

if TYPE_CHECKING:  # annotations only — `from __future__ import annotations` defers them
    from cadless.scoped_store import AnyStore
    from cadless.store import ScriptVersion


def race_and_judge(
    pipeline,
    intent: str,
    *,
    n: int,
    provider=None,
    critic=None,
    assertions: GeometryAssertions | None = None,
    signature_of: SignatureOf | None = None,
    export_dir: str | None = None,
    grounding: str | None = None,
    images=(),
    on_reading=None,
) -> tuple[JudgeResult, list[GenerationResult]]:
    """Fan out ``n`` candidates and judge them, returning the verdict and the field.

    This is the single composition both the live agent turn and the evaluation
    harness go through. Keeping it in one place is what makes an A/B of forge a
    measurement of the shipped behaviour rather than of a reimplementation that
    happens to resemble it — two copies would drift, and the copy that drifts is
    the one being measured.

    Every judging dependency is optional and each unset one costs a rung: with no
    ``provider`` the cheap-LLM tie-break cannot run, with no ``critic`` the render
    critique cannot, and with no ``assertions``/``signature_of`` pair the geometry
    rung cannot. Passing none of them leaves only the hard filter, and the ladder
    then falls through to input order — a race that selects arbitrarily while
    still paying for every candidate.

    The full candidate list comes back alongside the verdict because the losers
    are needed after the fact: the live path persists them, and the eval counts
    what the race actually spent.
    """
    candidates = pipeline.run_candidates(
        intent,
        n=n,
        export_dir=export_dir,
        assertions=assertions,
        grounding=grounding,
        images=images,
        on_reading=on_reading,
    )
    judged = select_winner(
        candidates,
        intent=intent,
        assertions=assertions,
        signature_of=signature_of,
        critic=critic,
        provider=provider,
    )
    return judged, candidates


async def persist_losers(
    store: AnyStore,
    project_id: int,
    intent: str,
    losers: list[GenerationResult],
    *,
    winner_version_id: int,
    parent_version_id: int | None = None,
) -> list[ScriptVersion]:
    """Persist the losing candidates of a race as NON-CURRENT rows (C4).

    The live chat path persists the forge WINNER through its normal tool-version
    path (set as current, artifacts copied). This records the remaining candidates
    as non-current ``script_versions`` rows tied to the winning sibling via
    ``candidate_of_version_id`` (and sharing ``parent_version_id``), so the race is
    retrievable yet the loser rows are never current.
    """
    persisted: list[ScriptVersion] = []
    for cand in losers:
        loser_version = await store.add_version(
            project_id,
            intent,
            cand.code,
            cand.ok,
            cand.error,
            cand.volume,
            cand.bbox,
            parameters=cand.parameters,
            parent_version_id=parent_version_id,
            candidate_of_version_id=winner_version_id,
        )
        persisted.append(loser_version)
    return persisted
