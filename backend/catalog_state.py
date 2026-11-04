"""Tell whether a project is catalog-derived (read-only) —.

Catalog items are loaded as ordinary projects and marked with the catalog item
they came from, on the project row (``projects.catalog_item_id``). Catalog items
are *read-only*: their parameters can be viewed but not edited; a user customizes
a catalog item by cloning it first, and the clone carries no mark, so it is
editable. The helpers here read that mark so the API can flag catalog projects
and refuse mutations on them.

The mark used to live in the sidecar ledger beside the store db, which every
mutating route had to open and parse per request. One unreadable copy of that file
therefore took the project list, chat, generate, rerun and revert down with it —
none of which are about catalog items — and answering "no catalog items" instead
would have made every one of them editable. Asking the db costs a
query the response is usually making anyway, and cannot half-fail. What is left
in the ledger is what the catalog pages *display*, and the domain that says which
unit an item's scripts are written in; see ``cadless.catalog.ledger``.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import HTTPException

from cadless.catalog.domains import find_domain
from cadless.catalog.ledger import Ledger, LedgerUnreadable
from cadless.scoped_store import ScopedStore
from cadless.store import Project

logger = logging.getLogger(__name__)


def ledger_for(store: ScopedStore) -> Ledger:
    """The catalog ledger that sits beside the active store's database."""
    return Ledger(Path(store.db_path).parent / "catalog-ledger.json")


async def catalog_metadata(store: ScopedStore) -> dict[str, dict]:
    """The catalog's display metadata, read off the event loop.

    The catalog pages need this, and so does :func:`authoring_units` for a
    project descending from a catalog item. Routes deciding whether a project is
    read-only must not call it — that is what put a JSON parse on the path of
    every write. Raises ``LedgerUnreadable`` if the file is damaged; its callers
    degrade to what the db knows rather than failing the request.
    """
    return await asyncio.to_thread(ledger_for(store).entries)


async def authoring_units(store: ScopedStore, project: Project) -> str | None:
    """The unit ``project``'s scripts are written in, or ``None`` where it cannot be read.

    A project nothing was copied or branched from was written by the engine, in
    millimetres. One that was takes the unit of the catalog item its line goes back
    to: its versions keep the size the catalog filed in millimetres, while a build
    of the same script measures it in the script's own unit, so for an item written
    in metres the two are a thousand times apart. Only a project descending from a
    catalog item reads the ledger, and a ledger that cannot answer -- damaged, or no
    longer holding the item -- answers ``None`` rather than failing the request
    that asked: an unreadable ledger once took every write route down with it.

    The one case read as millimetres without being shown to be is a copy made
    before the database recorded where copies come from: it has no pointer to
    follow, so it looks like a project nothing was copied from.
    """
    seen: set[int] = set()
    while project.catalog_item_id is None:
        if project.id in seen:
            return None
        seen.add(project.id)
        source_id = project.derived_from_project_id
        if source_id is None and project.branched_from_version_id is not None:
            origin = await store.get_version(project.branched_from_version_id)
            if origin is None:
                return None
            source_id = origin.project_id
        if source_id is None:
            return "mm"
        source = await store.get_project(source_id)
        if source is None:
            return None
        project = source
    try:
        entry = (await catalog_metadata(store)).get(project.catalog_item_id)
    except (LedgerUnreadable, OSError, ValueError):
        # ValueError: bytes that are not text fail before the JSON parse that
        # turns a bad file into LedgerUnreadable.
        logger.warning(
            "catalog ledger unreadable; no unit for %s, so its copies show no size",
            project.catalog_item_id,
            exc_info=True,
        )
        return None
    key = entry.get("domain") if isinstance(entry, dict) else None
    domain = find_domain(key) if isinstance(key, str) else None
    return domain.authoring_units if domain is not None else None


async def is_catalog_project(store: ScopedStore, project_id: int) -> bool:
    """True if ``project_id`` is a catalog item (and therefore read-only)."""
    project = await store.get_project(project_id)
    return project is not None and project.catalog_item_id is not None


async def reject_if_catalog(
    store: ScopedStore, project_id: int, action: str, *, remedy: str | None = None
) -> None:
    """Refuse a mutation on a catalog item with the shared read-only 403.

    Every route that mutates a catalog item goes through here, so the refusal
    reads the same wherever a user meets it. ``action`` completes "Clone the item
    to ..." with what was being attempted.

    ``remedy`` replaces that second sentence and makes ``action`` unused. Cloning
    answers most of these — you get an editable copy of what you were denied —
    but it does not answer deletion: a copy is not a way to remove the original.
    """
    if await is_catalog_project(store, project_id):
        advice = remedy or f"Clone the item to {action}."
        raise HTTPException(
            status_code=403,
            detail=f"Catalog items are read-only. {advice}",
        )
