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
in the ledger is what the catalog pages *display*; see ``cadless.catalog.ledger``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import HTTPException

from cadless.catalog.ledger import Ledger
from cadless.scoped_store import ScopedStore


def ledger_for(store: ScopedStore) -> Ledger:
    """The catalog ledger that sits beside the active store's database."""
    return Ledger(Path(store.db_path).parent / "catalog-ledger.json")


async def catalog_metadata(store: ScopedStore) -> dict[str, dict]:
    """The catalog's display metadata, read off the event loop.

    Only the catalog pages need this. Routes deciding whether a project is
    read-only must not call it — that is what put a JSON parse on the path of
    every write. Raises ``LedgerUnreadable`` if the file is damaged; the catalog
    routes degrade to what the db knows rather than failing the request.
    """
    return await asyncio.to_thread(ledger_for(store).entries)


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
