"""House Catalog browse endpoint with discovery (#21).

Surfaces the catalog items that have been loaded into the backend so the UI can
show a browsable Catalog panel. Which projects those are comes from the db; the
details shown about each one come from the sidecar ledger (written by
``cadless.catalog`` at load time), whose path is derived from the active store's
data dir so it follows test/overridden stores. Losing the ledger therefore costs
this panel its details, not its contents.

Discovery (#21): ``q`` searches name/tags/description case-insensitively,
``domain``/``category`` filter exactly, and ``limit``/``offset`` paginate the
flat, deterministically ordered item list (registry domain order, then name).
``domains``/``categories`` facets drive the UI's filter chips — domain counts
are global while category counts follow the active domain filter. The legacy
``groups`` view (asserted by the #20 acceptance tests and pre-#21 clients) is
kept, grouping exactly the returned page.
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel

from backend.catalog_state import catalog_metadata, ledger_for
from backend.deps import get_store
from backend.unstoppable import to_completion
from cadless.catalog.domains import all_domains, domain_label, domain_sort_key
from cadless.catalog.importer import (
    RootScan,
    discard_item_dir,
    received_origins,
    scan_startup_catalog,
    unclaimed_places_of,
)
from cadless.catalog.ledger import Ledger, LedgerUnreadable
from cadless.catalog.loader import clear_house, remove_imported_house
from cadless.catalog.origins import ItemOrigin, all_origins, find_origin
from cadless.scoped_store import ScopedStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/catalog", tags=["catalog"])

DEFAULT_LIMIT = 50
MAX_LIMIT = 200

# What an item's `source` may say, and how a chip spells it, is
# `cadless.catalog.origins` — a registry, so a build that ships another way of
# arriving is answered here without this file naming it. `local` is everything
# that did not arrive here — the bundled samples, work authored on this machine,
# and a catalogue a deployment mounted in their place. The tool does not invent
# a rule for telling those apart, and naming that `bundled` would be exactly
# such a rule, asserted about items it would often be wrong for.
LOCAL_SOURCE = "local"

# Said only of an item whose files are in the catalog this app loads at startup.
# It names what makes the removal pointless rather than where the item came
# from: a deployment mounts that root read-only, and a machine that authored the
# item there would load it straight back in.
BUNDLED_REFUSAL = (
    "This catalog item's files are in the catalog this app loads at startup, "
    "so removing it here would not outlast the next start."
)

# Said when a catalog root could not be read. Not a refusal about the item: the
# app cannot see where this one's files are, and clearing a record on the
# strength of not having looked is how an unreadable mount costs somebody the
# item behind it. Transient by nature, so it is answered as such.
CANNOT_TELL = (
    "This app cannot read its catalog right now, so it cannot tell whether "
    "this item's files are still here. Nothing was removed."
)


@dataclass(frozen=True)
class DiskView:
    """Where each id's files are, from one walk of each root.

    ``arrived`` doubles as the received root's answer and as what each item there
    says about its provenance, which is why that walk reads a second small file
    per item — asking twice would be two walks. ``complete`` is both roots'
    together: an id is only shown to be nowhere if both walks read what they
    walked.
    """

    arrived: dict[str, ItemOrigin]
    startup: RootScan
    complete: bool

    def nowhere(self, item_id: str) -> bool:
        """Whether this id has been *shown* to have nothing left on disk.

        Not there and not shown to be not there are different answers, and only
        one of them may be acted on. The startup catalog has to be there to
        answer at all: it ships with the app, so a missing one is a deployment
        that lost its mount rather than a machine that never had it — and every
        item it holds would otherwise read as a record with nothing behind it.
        The received root is the other way round: it exists only once something
        has been received, and somebody removing all of it is the very case this
        is asked for, so its absence is an answer.
        """
        if not (self.complete and self.startup.present):
            return False
        return item_id not in self.arrived and not self.startup.claims(item_id)


def _disk_view() -> DiskView:
    """Walk both catalog roots once each. Off the loop — see `_live_items`."""
    unread: list[Path] = []
    arrived = received_origins(unread)
    startup = scan_startup_catalog()
    return DiskView(arrived=arrived, startup=startup, complete=not unread and startup.complete)


class CatalogItem(BaseModel):
    house_id: str
    name: str
    project_id: int
    current_version_id: int | None
    steps: int
    domain: str
    # Discovery metadata (#21); safe defaults for pre-#21 ledger entries.
    category: str | None = None
    tags: list[str] = []
    description: str | None = None
    thumbnail_url: str | None = None
    # Whether the app can take this item off this machine — true for one that
    # arrived as a package, and for a record whose files are gone, which is all
    # there is left to remove. False only for an item in the catalog loaded at
    # startup, which would be back at the next one.
    removable: bool = False
    # Set when nothing on disk claims this id any more. `removable` says the
    # panel may offer to remove it; this says what removing it takes, which is
    # the record and nothing else — the copy is already gone.
    files_missing: bool = False
    # Where the copy came from, as the item's own provenance records it. `None`
    # means it did not say — which is not the same as any of the answers, and
    # picking one of them for it would be a claim made out of not knowing.
    #
    # Which catalogue an arrived item came from is deliberately not here.
    # Nothing on this page needs it, and the panel that does asks
    # `/catalog/origins/{kind}`, which answers by `house_id` — so it can be
    # joined back onto these items by anything that later wants it, without a
    # field going out on a public response ahead of a reader for it.
    source: str | None = None


class CatalogGroup(BaseModel):
    domain: str
    label: str
    items: list[CatalogItem]


class CatalogFacet(BaseModel):
    key: str
    label: str
    count: int


class DomainOut(BaseModel):
    """One registered domain, as something to offer rather than something found.

    A facet counts what is here; this says what there is to ask for. No count,
    because there is nothing to count — the answer describes the registry, not
    a collection.
    """

    key: str
    label: str


class DomainsOut(BaseModel):
    domains: list[DomainOut]


class CatalogOut(BaseModel):
    groups: list[CatalogGroup]
    items: list[CatalogItem]
    total: int
    limit: int
    offset: int
    domains: list[CatalogFacet]
    categories: list[CatalogFacet]
    sources: list[CatalogFacet] = []
    # True when the item details (tags, category, thumbnails, step counts) could
    # not be read and the listing is names only, so the panel can say so instead
    # of presenting a stripped catalog as if it were the whole one.
    details_unavailable: bool = False


class OriginOut(BaseModel):
    """One way an item can have arrived, as this build presents it."""

    key: str
    label: str


class OriginsOut(BaseModel):
    origins: list[OriginOut]


class HeldOrigin(BaseModel):
    """One held item, in the terms the origin it came from answers for."""

    house_id: str
    catalog_id: str
    version_id: str | None = None
    # Which version of it is the copy here. A source that publishes the same
    # value per version makes this compare without asking it anything.
    digest: str | None = None


class HeldOriginsOut(BaseModel):
    items: list[HeldOrigin]


def _source_of(arrived: ItemOrigin | None, *, on_disk: bool = True) -> str | None:
    """What to tell the panel about where one item came from.

    Not among the things that arrived is ``local`` — but only for an item that
    is here to be looked at. A record whose files are gone is also absent from
    that walk, and calling it local would be a claim about somebody's item made
    on the strength of no longer having it: the copy this listing describes may
    well have arrived from somewhere else. ``on_disk`` is what tells those apart.

    An item that did arrive but whose provenance could not be read gets nothing
    either — the answers here are claims, and none of them is the one to make
    when the item never said. The same goes for an item recorded by a build this
    one does not have: its origin is not registered here, so there is no label to
    put on it and no filter it could answer.
    """
    if arrived is None:
        return LOCAL_SOURCE if on_disk else None
    return arrived.kind if find_origin(arrived.kind) else None


def _matches(item: CatalogItem, needle: str) -> bool:
    """Case-insensitive substring search over name, tags, and description."""
    if needle in item.name.lower():
        return True
    if any(needle in tag.lower() for tag in item.tags):
        return True
    return bool(item.description and needle in item.description.lower())


async def _live_items(store: ScopedStore) -> tuple[list[CatalogItem], bool]:
    """Every loaded catalog item in stable UI order, and whether details are missing.

    Which projects are catalog items comes from the db, and only what the panel
    *displays* comes from the ledger. So a ledger nobody can parse
    costs this page its tags, categories and thumbnails — the items themselves
    still list, under the names their projects carry. It used to cost the whole
    response a 500, along with every other route that read the same file.
    """
    loaded = await store.catalog_item_ids()  # project_id -> house_id
    try:
        entries = await catalog_metadata(store)
    except LedgerUnreadable:
        logger.exception("catalog metadata unreadable; listing items without details")
        entries = {}
    # Whether details are missing is answered by what actually arrived, not by
    # whether reading threw: the file may equally have been moved aside at startup,
    # or be a stale one that has lost entries. Either way the panel should say the
    # listing is names only rather than present it as the whole catalog.
    details_ok = all(hid in entries for hid in loaded.values())
    # The thumbnail is baked onto one specific version, so ask the artifact table
    # which one rather than assuming it is still the current version — moving
    # current used to leave the URL pointing at a version that never had it.
    # One batched lookup, so the list does not gain a query per item.
    thumbnail_versions = await store.thumbnail_version_ids(
        [pid for pid, hid in loaded.items() if (entries.get(hid) or {}).get("thumbnail")]
    )
    # Which items arrived here — and, for each, what it says about where it came
    # from — is one walk of the received root, asked once for the whole response
    # and off the loop, like the ledger above it. Deliberately not the ledger's
    # copy of the same provenance: a ledger nobody can parse costs this page its
    # tags, and it must not also cost every received item its origin, which is
    # what a filter is about to be built on.
    #
    # The other root is walked here too, for the difference between "the app
    # cannot remove this" and "removing it is all that is left to do" — which the
    # panel has no other way to tell. Neither claim is made from a walk that did
    # not read what it walked: a root that went unread leaves every item under it
    # looking like a record with no files, and the panel would offer to clear
    # items that are sitting right there.
    disk = await asyncio.to_thread(_disk_view)
    origins = disk.arrived
    items: list[CatalogItem] = []
    for pid, house_id in loaded.items():
        entry = entries.get(house_id) or {}
        project = await store.get_project(pid)
        if project is None:
            continue  # raced with a delete
        cvid = project.current_version_id
        # Both roots can answer to one id — a bundled sample and a received item
        # of the same name — and the received copy is the one a removal takes,
        # so arriving here wins over being in the startup catalog.
        arrived = house_id in origins
        files_missing = disk.nowhere(house_id)
        thumbnail_vid = thumbnail_versions.get(pid) if entry.get("thumbnail") else None
        thumbnail_url = (
            f"/versions/{thumbnail_vid}/artifacts/thumbnail" if thumbnail_vid is not None else None
        )
        items.append(
            CatalogItem(
                house_id=house_id,
                name=entry.get("name") or project.name,
                project_id=pid,
                current_version_id=cvid,
                steps=int(entry.get("step_count", 0)),
                domain=entry.get("domain") or "house",
                category=entry.get("category"),
                tags=list(entry.get("tags") or []),
                description=entry.get("description"),
                thumbnail_url=thumbnail_url,
                removable=arrived or files_missing,
                files_missing=files_missing,
                source=_source_of(origins.get(house_id), on_disk=not files_missing),
            )
        )
    # Registry-driven domain order, then name — a deterministic global order so
    # pagination windows never overlap or skip.
    items.sort(key=lambda it: (domain_sort_key(it.domain), it.name.lower(), it.house_id))
    return items, details_ok


@router.get("/domains", response_model=DomainsOut)
async def get_domains() -> DomainsOut:
    """Every domain this build knows, in the order it presents them.

    The catalog listing answers domains as facets, which is the right answer to
    "what is here" and the wrong one to "what can be asked for": a facet exists
    only where an item does, so a machine holding houses would offer no way to
    look for anything else. A listing somewhere else holds what other people
    published, and narrowing it by a domain nothing local happens to use is
    exactly the case a facet cannot serve.

    Answered from the registry so the labels and their order have one source.
    A copy of the table in the frontend would be a second one, and the two would
    disagree the first time a domain is added.
    """
    return DomainsOut(
        domains=[DomainOut(key=domain.key, label=domain.label) for domain in all_domains()]
    )


@router.get("", response_model=CatalogOut)
async def get_catalog(
    q: str | None = Query(default=None, description="search name/tags/description"),
    domain: str | None = Query(default=None),
    category: str | None = Query(default=None),
    source: str | None = Query(default=None, description="where the item came from"),
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    store: ScopedStore = Depends(get_store),
) -> CatalogOut:
    """Catalog items (only those still present as projects), searchable + paginated."""
    items, details_ok = await _live_items(store)

    domain_counts = Counter(it.domain for it in items)
    domains = [
        CatalogFacet(key=key, label=domain_label(key), count=domain_counts[key])
        for key in sorted(domain_counts, key=domain_sort_key)
    ]

    # Counted over the whole catalog like domains, so clicking one chip does not
    # renumber the rest. Only the answers an item can actually carry get a chip:
    # an item that did not say is not gathered under a name it never took.
    source_counts = Counter(it.source for it in items if it.source is not None)
    sources = [
        CatalogFacet(key=origin.key, label=origin.label, count=source_counts[origin.key])
        for origin in all_origins()
        if source_counts[origin.key]
    ]

    scoped = [
        it
        for it in items
        if (domain is None or it.domain == domain) and (source is None or it.source == source)
    ]
    category_counts = Counter(it.category for it in scoped if it.category is not None)
    categories = [
        CatalogFacet(key=key, label=key, count=category_counts[key])
        for key in sorted(category_counts)
    ]

    filtered = scoped
    if category is not None:
        filtered = [it for it in filtered if it.category == category]
    if q:
        needle = q.lower()
        filtered = [it for it in filtered if _matches(it, needle)]

    page = filtered[offset : offset + limit]

    # Legacy grouped view of the returned page (kept for pre-#21 clients).
    by_domain: dict[str, list[CatalogItem]] = defaultdict(list)
    for it in page:
        by_domain[it.domain].append(it)
    groups = [
        CatalogGroup(domain=d, label=domain_label(d), items=by_domain[d])
        for d in sorted(by_domain, key=domain_sort_key)
    ]

    return CatalogOut(
        groups=groups,
        items=page,
        total=len(filtered),
        limit=limit,
        offset=offset,
        domains=domains,
        categories=categories,
        sources=sources,
        details_unavailable=not details_ok,
    )


@router.get("/origins", response_model=OriginsOut)
async def get_origins() -> OriginsOut:
    """Every way an item can have arrived, in the order this build presents them.

    The same reasoning as `/domains` above, and for the same reason it is not
    the listing's facets: a facet exists only where an item does, so a machine
    that has received nothing would offer no way to say what it is looking at.

    Answered from the registry so the labels and their order have one source. A
    copy of the table in the frontend would be a second one, and the two would
    disagree the first time a build adds a way of arriving — which is precisely
    what the registry exists to allow.
    """
    return OriginsOut(
        origins=[OriginOut(key=origin.key, label=origin.label) for origin in all_origins()]
    )


@router.get("/origins/{kind}", response_model=HeldOriginsOut)
async def get_held_origins(kind: str, store: ScopedStore = Depends(get_store)) -> HeldOriginsOut:
    """Every item this machine already holds a copy of, from one origin.

    Its own route rather than a filter on the listing above, because a panel
    asking needs all of them at once to mark a page of search results, and the
    listing is paginated — a panel reading it would either page through the
    whole catalog or quietly mark only the first hundred.

    It is also cheaper than that listing for the same reason it is separate: no
    project row is needed to answer this, only which ids are loaded and what the
    received root says about each.

    An unregistered kind is a 404 rather than an empty list. Empty would say
    "you hold none of those", which is a different answer from "this build has
    never heard of that", and a panel that got the first would stop asking.
    """
    if find_origin(kind) is None:
        raise HTTPException(status_code=404, detail=f"no origin registered as {kind!r}")
    loaded = set((await store.catalog_item_ids()).values())
    origins = await asyncio.to_thread(received_origins)
    return HeldOriginsOut(
        items=[
            HeldOrigin(
                house_id=house_id,
                catalog_id=origin.catalog_id,
                version_id=origin.version_id,
                digest=origin.digest,
            )
            for house_id, origin in sorted(origins.items())
            # Without a catalogue id there is nothing to match, and an entry
            # here that matches nothing is one the panel would compare against
            # every listing forever.
            if house_id in loaded and origin.kind == kind and origin.catalog_id
        ]
    )


@router.delete("/{house_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_catalog_item(
    house_id: str, request: Request, store: ScopedStore = Depends(get_store)
):
    """Take a catalog item off this machine — whatever is left of it here.

    The read-only gate on a project refuses deleting a catalog item, and this is
    where they go instead. Three answers, because where an item's files sit is
    three different situations and only one of them is a refusal:

    A received item goes in full — project, entry, and files. It has to take all
    three: the entry alone left behind is what made a deleted item skip its next
    load and refuse a fresh import of the same package.

    An item in the catalog this app loads at startup stays. Removing it would
    last only until that root was walked again, and the deployment mounts it
    read-only, so there is nothing here this app may take.

    An id no directory anywhere claims is a record with nothing left to
    describe, and clearing it is the only thing that ends it: the project row
    outlives the files, `load_house` has nothing to rebuild it from, and the
    entry goes on refusing every fresh import of that name. This is the case the
    import route's replace path already treats this way (`_make_room`), and the
    one this route used to fold in with the bundled refusal above.

    That third answer is reached only from a walk that read everything it meant
    to. An id is missing from a walk that could not list a root, could not read a
    directory, or could not open a manifest, exactly as it is missing from one
    that found nothing — and deleting on the strength of not having looked is how
    an unreadable mount costs somebody the bundled item behind it. Where the app
    cannot tell, it says so and takes nothing.

    Under the import gate, which an import already holds while it decides that a
    name is free and then takes it. Removal decides the same thing about the same
    names and then gives one up, so running beside an import would let this one
    carry off the directory that one had just put in place. Holding it also means
    the answers cannot change between asking and acting.

    Held by a task of its own rather than by this request, for the reason
    `backend.routers.packages.place_package` describes: the removals go to worker threads
    that cancelling cannot reach, so an `async with` here would hand the gate to
    the next import while this one was still taking directories away underneath
    it — the same collision from the other side.
    """
    # Whether the id names a catalog item is the db's answer, like everywhere else
    # that decides it. Asking the ledger would make a removal fail on a file that
    # has nothing to do with what is being removed — and answer 404 for an item
    # that is plainly here.
    if house_id not in (await store.catalog_item_ids()).values():
        raise HTTPException(status_code=404, detail="catalog item not found")
    ledger = ledger_for(store)

    async def guarded() -> None:
        async with request.app.state.import_gate:
            await _remove_under_the_gate(store, ledger, house_id)

    await to_completion(guarded())


async def _remove_under_the_gate(store: ScopedStore, ledger: Ledger, house_id: str) -> None:
    """The removal itself, which must finish once it starts. The caller holds the gate."""
    # One walk decides "did this arrive here" and does the removal, so nothing
    # can change underneath between asking and acting.
    if await remove_imported_house(store, ledger, house_id):
        return
    # Asked here rather than inside the loader: `_make_room` reads that
    # function's ``False`` as "no id answers to this directory" and discards
    # the directory itself, so an id cleared in there would strand one.
    disk = await asyncio.to_thread(_disk_view)
    if disk.startup.claims(house_id):
        raise HTTPException(status_code=403, detail=BUNDLED_REFUSAL)
    if not disk.nowhere(house_id):
        # A root went unread, the startup catalog is not there to be read at
        # all, or the received root holds this id after the removal above
        # said it does not. None of them is anything about the item, so this
        # is not a refusal aimed at what was asked for.
        raise HTTPException(status_code=503, detail=CANNOT_TELL)
    # Nothing claims the id. A directory can still hold the place an import
    # of it would take — one whose manifest is not json, or which has none —
    # and leaving that would make "you can receive it again" untrue.
    try:
        for stranded in await asyncio.to_thread(unclaimed_places_of, house_id):
            await asyncio.to_thread(discard_item_dir, stranded)
    except OSError as exc:
        # Nothing has been cleared yet, so the item is exactly as it was and
        # saying so is the honest answer — the same one `_make_room` gives
        # when it cannot take away what is in the way.
        logger.exception("could not take away what stands in place of %s", house_id)
        raise HTTPException(status_code=503, detail=CANNOT_TELL) from exc
    await clear_house(store, ledger, house_id)
