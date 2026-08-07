"""Load / clear catalog houses into the backend Store (Phase 1).

A house is loaded by creating a project, adding one version per ladder step
(chaining ``parent_version_id``), copying each step's pre-rendered artifacts into
the store and registering them, replaying the instructions into the chat session,
and pointing the project's current version at the final step. The sidecar ledger
records the mapping so ``load`` is idempotent and ``clear`` is precise.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import shutil
from pathlib import Path

from cadless.catalog.importer import discard_item_dir, imported_item_dir
from cadless.catalog.ledger import Ledger, LedgerBusy, LedgerUnreadable
from cadless.catalog.manifest import (
    CatalogManifest,
    discover_houses,
    load_manifest,
    read_source_json,
)
from cadless.params import extract_params
from cadless.scoped_store import AnyStore, system_view
from cadless.store import Store

logger = logging.getLogger(__name__)


def item_content_hash(house_dir: Path, manifest: CatalogManifest | None = None) -> str:
    """Content hash of an item's manifest + step code + provenance (#23).

    The manifest embeds baked geometry/artifact/thumbnail state, so any re-bake
    or re-author changes this hash even when the step code is untouched;
    ``source.json`` is included so a provenance-only correction also counts as
    a change. Pass an already-loaded ``manifest`` to skip re-parsing it.
    """
    house_dir = Path(house_dir)
    manifest = manifest or load_manifest(house_dir)
    digest = hashlib.sha256()
    digest.update((house_dir / "manifest.json").read_bytes())
    for step in manifest.steps:
        digest.update((house_dir / step.code).read_bytes())
    source_path = house_dir / "source.json"
    if source_path.exists():
        digest.update(source_path.read_bytes())
    return "sha256:" + digest.hexdigest()


def _copy_artifact(store: AnyStore, version_id: int, src: Path, filename: str) -> str | None:
    """Copy one baked artifact into the store, or report there was none.

    The existence check, the directory it lands in and the copy itself are all
    filesystem work, so they cross to the worker thread together rather than
    straddling it. A source that is not there creates no directory, exactly as
    when this ran inline.
    """
    if not src.exists():
        return None
    dst = Path(store.version_artifact_dir(version_id)) / filename
    shutil.copyfile(src, dst)
    return str(dst)


async def load_house(
    store: AnyStore, ledger: Ledger, house_dir: Path, *, reload: bool = False
) -> int | None:
    """Insert one house; return the new project id, or ``None`` if skipped.

    Incremental (#23): an already-loaded item whose recorded ``content_hash``
    still matches is skipped; a changed item is reloaded in place. Entries
    recorded before content hashing (no ``content_hash``) keep the legacy
    behavior — always skipped unless ``reload`` is set (without paying for
    a hash).

    Loaded as the build regardless of who is asking. A catalogue item lives in a
    shared directory and is read-only, so it belongs to the installation rather
    than to whoever triggered the load — and normalising here rather than at each
    of the four call sites is what stops the same item being private when it
    arrived one way and shared when it arrived another. What a user makes *from*
    one is theirs, because customising is a clone.
    """
    store = system_view(store)
    house_dir = Path(house_dir)
    # Reading the manifest, hashing every step and copying what was baked is far
    # more filesystem work than an event loop should do inline — an import runs
    # while the api service is answering other requests, the worker's among
    # them. Each piece goes to a worker thread on its own, which leaves the
    # order the store and the ledger see exactly as it was.
    manifest = await asyncio.to_thread(load_manifest, house_dir)
    # Whether this item is already loaded is the db's answer, not the ledger's. A
    # ledger that cannot be read would otherwise report every item as absent and
    # import the whole catalog again, leaving the first copy behind as an ordinary
    # editable project.
    loaded_as = await store.project_id_for_catalog_item(manifest.id)
    content_hash: str | None = None
    if loaded_as is not None and not reload:
        stored = await asyncio.to_thread(_stored_content_hash, ledger, manifest.id)
        if stored is None:
            # Either a legacy entry (pre-#23) or a ledger we cannot read: both
            # mean "no hash to compare", and skipping is what a legacy entry has
            # always done. Re-importing on a bad ledger would be the worse guess.
            return None
        content_hash = await asyncio.to_thread(item_content_hash, house_dir, manifest)
        if stored == content_hash:
            return None
        reload = True  # content changed on disk: refresh the loaded copy
    if reload and loaded_as is not None:
        await clear_house(store, ledger, manifest.id)
    if content_hash is None:
        content_hash = await asyncio.to_thread(item_content_hash, house_dir, manifest)

    # Marked as a catalog item in the same insert that creates it, so there is no
    # moment where it exists on disk as an ordinary, editable project.
    project = await store.create_project(manifest.name, catalog_item_id=manifest.id)
    session = await store.get_or_create_session(project.id)
    parent: int | None = None
    last_vid: int | None = None
    for step in manifest.steps:
        code = await asyncio.to_thread((house_dir / step.code).read_text)
        # Surface the script's editable ``params`` block the same way
        # the live generation path does, so catalog items are parametric in the UI.
        version = await store.add_version(
            project.id,
            step.instruction,
            code,
            ok=True,
            volume=step.geometry.volume,
            bbox=step.geometry.bbox,
            parameters=extract_params(code),
            parent_version_id=parent,
        )
        for kind, rel in step.artifacts.items():
            dst = await asyncio.to_thread(
                _copy_artifact, store, version.id, house_dir / rel, f"model.{kind}"
            )
            if dst is not None:
                await store.add_artifact(version.id, kind, dst)
        # Replay a realistic conversation when the step carries a re-authored
        # transcript; otherwise fall back to the legacy placeholder.
        if step.transcript is not None:
            user_text = step.transcript.user_prompt
            assistant_text = step.transcript.assistant_message
        else:
            user_text = step.instruction
            assistant_text = f"Built step {step.index}: {step.instruction}"
        await store.add_message(session.id, "user", user_text)
        await store.add_message(
            session.id,
            "assistant",
            assistant_text,
            version_id=version.id,
        )
        parent = version.id
        last_vid = version.id

    if last_vid is not None:
        await store.set_current_version(project.id, last_vid)
    # The baked item thumbnail (#21) rides on the final version so the API can
    # serve it through the ordinary per-version artifact route.
    has_thumbnail = False
    if manifest.thumbnail and last_vid is not None:
        src = house_dir / manifest.thumbnail
        dst = await asyncio.to_thread(_copy_artifact, store, last_vid, src, "thumbnail.png")
        if dst is not None:
            await store.add_artifact(last_vid, "thumbnail", dst)
            has_thumbnail = True
    source = await asyncio.to_thread(read_source_json, house_dir)
    # Recording the display metadata comes after the item is in and marked, and a
    # ledger that cannot be read-modify-written must not undo that: the project is
    # loaded and read-only either way, and what is missing is what the catalog
    # panel shows about it. Reporting the load as a failure would invite a retry
    # that the db would then correctly skip, leaving the same gap.
    #
    # `LedgerBusy` joins it for the same reason: waiting the other writer out is
    # what must not happen here, since this runs with the import gate held
    # (`backend/routers/packages.py`).
    #
    # What is left behind outlasts the next start. A row saying "already loaded"
    # with no entry beside it is skipped above for good — `clear_house` says the
    # same thing from the other end — so the item lists under its project's name
    # with no tags, no category, no thumbnail and a step count of zero, and the
    # whole listing reports its details as incomplete. `catalog reload` is what
    # rewrites them, which is the remedy the startup path already names for the
    # same state after an unreadable ledger is moved aside.
    try:
        await asyncio.to_thread(
            ledger.record,
            manifest.id,
            len(manifest.steps),
            name=manifest.name,
            domain=manifest.domain,
            category=manifest.category,
            tags=manifest.tags,
            description=manifest.description,
            thumbnail=has_thumbnail,
            source=source,
            content_hash=content_hash,
        )
    except LedgerUnreadable:
        logger.exception("loaded %s but could not record its catalog details", manifest.id)
    except LedgerBusy:
        # A condition this asked for, not a fault: reported at the level the
        # clear path uses for the same thing, without a traceback that would
        # read as a crash on every import a CLI run happened to overlap.
        logger.warning(
            "loaded %s but another process held the catalog ledger; "
            "its details are missing until `catalog reload`",
            manifest.id,
        )
    return project.id


def _stored_content_hash(ledger: Ledger, house_id: str) -> str | None:
    """The recorded content hash, or ``None`` if there isn't one to compare.

    A ledger we cannot read has no hash to offer, and saying so is more useful
    here than propagating the parse error: incremental reload is an optimisation,
    and losing it degrades to "skip", never to "import a second copy".
    """
    try:
        entry = ledger.get(house_id)
    except LedgerUnreadable:
        logger.warning("catalog ledger unreadable; skipping change detection for %s", house_id)
        return None
    return entry.get("content_hash") if entry else None


async def clear_house(store: AnyStore, ledger: Ledger, house_id: str) -> bool:
    """Delete a catalog house's project and its ledger entry. Self-heals either half.

    Which project to delete comes from the db, where the ledger used to be asked —
    an unreadable one made a loaded item undeletable, and a reload would then add a
    second copy beside the one it could not clear. Both halves are cleared
    independently, and having found only one of them still counts as having cleared
    the item: a project whose entry went missing is as much a leftover as an entry
    whose project was deleted behind its back, and neither should survive a clear.

    The project row goes first, and which half that is decides what an
    interruption costs. Stopping in between leaves the row gone and the entry
    stale, and whether an item is loaded is the row's answer — so the next start
    reads that as "not loaded" and builds the item again, entry and all.
    Forgetting the entry first would leave the opposite half: a row still saying
    "already loaded" with no recorded hash to compare against, which `load_house`
    skips for good. The item would sit there read-only and detail-less until
    somebody reloaded it by hand, which is not something the app offers a way to
    ask for.
    Cleared as the build, matching :func:`load_house`. A catalogue item is the
    installation's, so clearing one reaches the installation's row and stops
    there — a user's own project is not a catalogue item and is not this
    function's to delete, whichever store it was handed.
    """
    store = system_view(store)
    project_id = await store.project_id_for_catalog_item(house_id)
    if project_id is not None:
        await store.delete_project(project_id)  # ignore bool: may already be gone
    forgot = await asyncio.to_thread(_forget, ledger, house_id)
    return project_id is not None or forgot


def _forget(ledger: Ledger, house_id: str) -> bool:
    """Drop a ledger entry if there is one, saying whether there was.

    Tolerates a ledger that cannot be read, or one another process is mid-write
    in: by the time this runs the project row is already gone, so the item is
    cleared as far as anything deciding read-only access is concerned, and what
    is left behind is a metadata entry the next load overwrites. Waiting the
    other writer out is not on offer — a clear reached from an import holds the
    import gate, so this must not park behind a process it knows nothing about.
    """
    try:
        if ledger.get(house_id) is None:
            return False
        ledger.remove(house_id)
    except LedgerUnreadable:
        logger.warning("catalog ledger unreadable; left a stale entry for %s", house_id)
        return False
    except LedgerBusy:
        logger.warning(
            "catalog ledger held by another process; left a stale entry for %s", house_id
        )
        return False
    return True


async def remove_imported_house(store: AnyStore, ledger: Ledger, house_id: str) -> bool:
    """Take a received item off this machine: project, ledger entry, and files.

    ``False`` for an id no received item answers to — a bundled one among them.
    Those ship with the image on a read-only mount, and clearing one would last
    only until the next load walked that root again.

    The project goes before the files. `import_package` refuses an id the store
    already holds a project for *and* a directory that still exists, so stopping
    between the two the other way round would leave the item impossible to remove
    and impossible to receive again — the dead end this exists to close. In this
    order an interruption leaves the files behind for the next load to pick back
    up, which marks a fresh project for them. (What the refusal reads is the
    ``catalog_item_id`` column; the ledger entry `clear_house` also drops is the
    item's display metadata, and losing it early — with the row already gone,
    which is the order `clear_house` keeps — costs nothing a load will not
    rewrite.)

    `discard_item_dir` keeps that true for a delete that fails rather than one
    that is interrupted: it moves the directory out of the scanned root before
    trying to delete it, so a failure cannot leave a half-deleted item sitting
    where the next import would refuse to write.
    """
    item_dir = await asyncio.to_thread(imported_item_dir, house_id)
    if item_dir is None:
        return False
    await clear_house(store, ledger, house_id)  # ignore bool: the entry may already be gone
    await asyncio.to_thread(discard_item_dir, item_dir)
    return True


async def load_all(
    store: AnyStore,
    ledger: Ledger,
    catalog_dir: Path,
    *,
    reload: bool = False,
) -> dict[str, int | None]:
    """Load every item in a directory; return each one's new project id or None.

    One unreadable item costs only itself. `discover_houses` counts any
    directory holding a manifest, and items arrive from outside now as well as
    being authored here — so a manifest this version cannot parse is not
    necessarily one anybody here wrote, and it must not take the rest of
    somebody's catalog down with it. A failure is logged rather than raised, and
    that item gets the same ``None`` a skipped one gets: this reports what was
    newly loaded, and neither of them was.
    """
    results: dict[str, int | None] = {}
    for house_id in discover_houses(catalog_dir):
        item_dir = Path(catalog_dir) / house_id
        try:
            results[house_id] = await load_house(store, ledger, item_dir, reload=reload)
        except Exception:
            logger.exception("catalog item could not be loaded: %s", item_dir)
            results[house_id] = None
    return results


async def clear_all(store: AnyStore, ledger: Ledger) -> list[str]:
    """Clear every loaded catalog item.

    What is loaded comes from the db, so clearing still works — and still reaches
    everything — when the ledger cannot be read. Ledger-only ids are swept too:
    they are the leftovers of a project deleted without being cleared, and a clear
    that walked one source would leave whichever half the other knew about.
    """
    cleared: list[str] = []
    for house_id in sorted(await _clearable_ids(store, ledger)):
        if await clear_house(store, ledger, house_id):
            cleared.append(house_id)
    return cleared


async def _clearable_ids(store: AnyStore, ledger: Ledger) -> set[str]:
    """Every catalog id either source knows about."""
    ids = set((await store.catalog_item_ids()).values())
    try:
        ids |= set(await asyncio.to_thread(ledger.entries))
    except LedgerUnreadable:
        logger.warning("catalog ledger unreadable; clearing only what the db records")
    return ids


async def backfill_catalog_item_ids(store: Store, ledger: Ledger) -> int:
    """Copy a legacy ledger's project mapping onto ``projects.catalog_item_id``.

    For databases loaded before the column existed: their catalog items are
    ordinary-looking rows until this runs, so it runs at startup before anything
    serves a request. The ``project_id`` it reads is only written by versions that
    predate the column — on a ledger written since, there is none and there is
    nothing to do. Idempotent besides: only rows still holding NULL are touched,
    so a project a user cloned or a catalog item somebody cleared is left alone.
    Returns how many rows it marked.

    Raises :class:`LedgerUnreadable` if the ledger cannot be parsed. The caller
    decides what to do about that; marking nothing and carrying on would leave
    every catalog item editable, which is the outcome worth refusing.
    """
    entries = await asyncio.to_thread(ledger.entries)
    marked = 0
    for house_id, entry in entries.items():
        project_id = entry.get("project_id")
        if project_id is None:
            continue
        project = await store.get_project(project_id)
        if project is None or project.catalog_item_id is not None:
            continue
        await store.set_catalog_item_id(project_id, house_id)
        marked += 1
    if marked:
        logger.info("marked %d project(s) as catalog items from the ledger", marked)
    return marked


async def list_state(store: AnyStore, catalog_dir: Path) -> list[dict]:
    """One row per discovered house with whether it is currently loaded.

    "Loaded" means a project answers to that catalog id, which is a question for
    the db. Asking the ledger instead would report items as absent whenever that
    file was damaged, and as present whenever an entry outlived its project.

    Answers without creating a database. Nothing here is loaded until something
    loads it, so no store is the same answer as an empty one — and a command that
    only reports should not leave a database behind where the caller had none, in
    a directory it may not even be able to write. An older one it does find is
    brought up to date, since that is what reading its columns requires.
    """
    loaded: dict[str, int] = {}
    if Path(store.db_path).exists():
        await store.init()
        loaded = {house_id: pid for pid, house_id in (await store.catalog_item_ids()).items()}
    return [
        {"id": house_id, "loaded": house_id in loaded, "project_id": loaded.get(house_id)}
        for house_id in discover_houses(catalog_dir)
    ]
