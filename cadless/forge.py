"""Forge mode: persist the losing candidates of a best-of-N race (C4).

Pillar 4 racing — *branch, evaluate, keep the winner, discard the rest*. C1
(``Pipeline.run_candidates``) fans out N fresh candidates and C2 (``judge.select_winner``)
picks the surviving line; that fan-out + judge happens synchronously inside the agent
loop (``Agent._forge_generate``). This module is the persistence leg: the winner is
written by the agent's normal tool-version path (set as current, artifacts copied),
and :func:`persist_losers` records the remaining candidates as NON-CURRENT
``script_versions`` rows.

Each loser shares the winner's ``parent_version_id`` (a parallel checkpoint) and is
flagged with ``candidate_of_version_id`` pointing at the winning sibling, so the race
stays retrievable (via ``Store.list_candidate_versions`` / the ``/versions/{id}/candidates``
endpoint) yet a loser is never the current version. No new table — racing reuses the
parent pointer + the existing current-version flag (``projects.current_version_id``).
"""

from __future__ import annotations

from cadless.pipeline import GenerationResult
from cadless.scoped_store import AnyStore
from cadless.store import ScriptVersion


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
