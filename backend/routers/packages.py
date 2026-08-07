"""Taking in a package this machine was handed.

A `.cls` arrives here one of two ways: someone passed the file along — on a
drive, in a chat — or a build asked some service for it. The first needs
nothing but the file, and this module is the whole of that path. The second is
that build's business up to the moment it has bytes, and from there it hands
them to `place_package` below rather than carrying its own copy of these checks.

That split is why this is not part of whatever router does the fetching. What a
package must satisfy before it joins the catalog does not depend on where it
came from, and a build that fetches from nowhere still has to be able to open
one it was given. Two entrances, one set of checks, and the one that drifted
would be the one nobody was reading.
"""

from __future__ import annotations

import asyncio
import re
import tempfile
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any, BinaryIO

from fastapi import APIRouter, Depends, HTTPException, Request

from backend.catalog_state import ledger_for
from backend.deps import get_store
from backend.unstoppable import to_completion
from cadless.catalog.importer import (
    CatalogImportConflict,
    CatalogImportError,
    CatalogImportUnavailable,
    ImportResult,
    discard_item_dir,
    import_package,
    occupants_of,
)
from cadless.catalog.ledger import Ledger
from cadless.catalog.loader import clear_house, load_house, remove_imported_house
from cadless.catalog.pack import (
    MAX_UNCOMPRESSED_BYTES,
    ClsError,
    ClsPackage,
    read_cls,
)
from cadless.scoped_store import ScopedStore

router = APIRouter(prefix="/packages", tags=["packages"])


# What a received package may weigh. The reader will not expand an archive past
# `MAX_UNCOMPRESSED_BYTES`, so a container heavier than everything it could
# legally hold is not one of ours — and refusing it as it arrives is what keeps
# the check from spending the memory it exists to save.
MAX_IMPORT_BYTES = MAX_UNCOMPRESSED_BYTES

# How many packages may be arriving at once. Distinct from `import_gate`, which
# serialises writing to the catalog and is reached only after a body has been
# received in full: this is about how much a machine holds open on behalf of
# callers who have not signed in. Unlike the timeouts below, this one is read
# once — the semaphore built from it is kept on the app — so moving it in a test
# only takes effect before that app has served an import.
#
# Two, because this multiplies the largest thing an import does. Reading a
# package that unpacks to the ceiling was measured to peak around 255 MiB, so
# this is what decides whether the route can reach the container's memory limit
# — see the api service in `docker-compose.yml`, which is set against this
# number. Raising it costs more than it looks: the catalog write is serialised
# behind `import_gate` anyway, so what more of these buys is overlapping reads,
# not imports that finish sooner.
MAX_CONCURRENT_IMPORTS = 2

# How long a package waits for its turn before being turned away. A wait here
# costs a connection and nothing else — the body has not been read yet — so it
# is worth absorbing a short burst rather than refusing one.
IMPORT_ADMISSION_TIMEOUT = 5.0

# How long a package has to finish arriving once it has a place. Generous for
# what this endpoint is for — the app publishes on the loopback address, and the
# largest package in the bundled catalog is under a megabyte — while bounding
# how long any one caller can occupy a place it is not using. Without it the
# admission limit above becomes the thing worth attacking rather than the thing
# doing the protecting.
IMPORT_BODY_TIMEOUT = 120.0

# How much of a package is held in memory before the rest of it goes to disk.
# Every package in the bundled catalog is comfortably under this, so the usual
# import never writes a file; the ceiling above is a hundred and fifty times
# larger, and it is the packages approaching *that* which this keeps out of
# memory. Read as a module attribute so a test can move it.
SPOOL_THRESHOLD_BYTES = 4 * 1024 * 1024

# An imported item records the name of the file it came out of. That name is
# someone else's text: it is shown, and control characters in it would render
# as anything at all here and in the document the item keeps.
MAX_ORIGIN_LENGTH = 128
_UNPRINTABLE = re.compile(r"[\x00-\x1f\x7f]")

# What a package may arrive as. None of these are a type a form can be submitted
# as, which is the point: it is what makes a browser ask permission before
# sending one from another site.
PACKAGE_CONTENT_TYPES = frozenset(
    {"application/octet-stream", "application/zip", "application/x-zip-compressed"}
)


@router.post("/import")
async def import_catalog(
    request: Request,
    filename: str = "",
    expected_digest: str = "",
    store: ScopedStore = Depends(get_store),
) -> dict[str, Any]:
    """Take a `.cls` someone sent, check it, and put it in the catalog.

    No sign-in, deliberately. A package handed over directly — on a drive, over
    a chat — went through no upload gate anywhere, and that is exactly the
    delivery with nothing else in front of it. Asking for an account here
    would turn away the one case this endpoint exists for.

    The body is the package itself rather than a form field: there is one file,
    and a multipart parser is a dependency this project does not carry.
    ``expected_digest`` is what the sender says the package hashes to; it is the
    only thing that notices an edit made after whoever published it let go.
    """
    _require_package_type(request.headers.get("content-type", ""))
    async with _admitted(request):
        # Handed over rather than closed here. This opened it, but it is not the
        # one that finishes with it: the read runs in a worker thread, and a
        # cancelled request reaches its own `finally` while that thread is still
        # going — closing the file out from under a read whose failure then lands
        # in a future nobody is waiting on. `place_package` closes it when the read is
        # done, whether or not anyone is still listening. The other entrance
        # passes bytes that own nothing.
        return await place_package(
            request,
            store,
            await _received(request),
            origin=_origin(filename),
            expected_digest=expected_digest or None,
            closing=True,
        )


async def place_package(
    request: Request,
    store: ScopedStore,
    payload: bytes | BinaryIO,
    *,
    origin: str,
    recorded: Mapping[str, Any] | None = None,
    expected_digest: str | None,
    overwrite: bool = False,
    closing: bool = False,
) -> dict[str, Any]:
    """Check a package and put it in the catalog, live.

    Where a package came from is the caller's business; what happens to it once
    it is here is not, and it must not be. A second entrance with its own copy
    of these checks would be a second answer to what a package has to satisfy,
    and the one that drifted would be the one nobody was reading.

    ``recorded`` travels alongside ``origin`` and only one entrance has one: the
    ids the source it was fetched from answers for, keyed by that source's
    origin, so a listing can be recognised later as one already here. A package
    handed over directly has none, and passes none. This module never looks
    inside it — the origin that wrote it is the one that reads it back.

    ``overwrite`` is the person's answer about replacing what is here, whether
    it was asked after a refusal from this or before the fetch by a caller that
    could already tell. It is never a default — a fetch that replaced silently
    would throw away whatever its owner had done to their copy — and it is not a
    permission either: what may be removed to make room is decided here, not by
    the caller who set it.

    ``closing`` hands over the payload itself, for the entrance that opened one.
    Whoever closes it has to be whoever finishes with it, and after a cancellation
    that is not the caller: the read runs in a worker thread, so a caller closing
    it on the way out closes a file still being read. The other entrance passes
    bytes that own nothing and leaves this false.
    """
    ledger = ledger_for(store)

    # Checking whether an id is already here, writing the item out and loading it
    # into the store are three steps with awaits between them, and the checks only
    # read — they claim nothing. Two imports overlapping there both pass, and the
    # second then fails on the directory the first has already put in place: a 500
    # where the honest answer is that something here answers to that name. One at
    # a time makes the checks mean what they say. Only imports wait on this, and
    # only on each other.
    #
    # Run as a task of its own so cancelling this one cannot take the gate away
    # from a write still in progress. `asyncio.to_thread` hands the reading and
    # the writing to threads cancellation does not reach, and an `async with`
    # here would let go at the moment the await was interrupted — leaving the
    # worker on its way to `staging.rename` with the next import already past
    # the checks. Held here, the gate is released by the work finishing rather
    # than by the request going away, and that next import meets a refusal
    # instead of a collision.
    #
    # The read is inside the task and outside the gate: it must not still be
    # running when the payload is closed, and it must not make other imports
    # queue behind it — overlapping reads are what the admission limit is for.
    async def guarded() -> dict[str, Any]:
        try:
            package = await asyncio.to_thread(read_cls, payload, expected_digest=expected_digest)
        except ClsError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            if closing:
                # Closed by whoever finished with it, which after a cancellation
                # is this task and not the caller — a request that closed it on
                # its way out would close a file the read is still using.
                payload.close()  # type: ignore[union-attr]
        async with request.app.state.import_gate:
            return await _write_in(
                store, ledger, package, origin, recorded, overwrite, expected_digest
            )

    return await to_completion(guarded())


async def _write_in(
    store: ScopedStore,
    ledger: Ledger,
    package: ClsPackage,
    origin: str,
    recorded: Mapping[str, Any] | None,
    overwrite: bool,
    expected_digest: str | None,
) -> dict[str, Any]:
    """Write the package into the catalog and load it. The caller holds the gate.

    Its own function so the gate can be held by a task rather than by the
    request: everything here has to happen or not, and a cancelled request must
    not be able to stop it partway with the checks it passed already stale.
    """
    try:
        imported = await _attempt(store, package, origin, recorded)
    except CatalogImportConflict as exc:
        if not overwrite:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        # Still inside the gate the caller holds. Removing reaches for the same
        # lock, and it is a plain one, so taking it again here would deadlock
        # the request rather than fail it.
        #
        # The refusal above names the first thing in the way, and there can be
        # more than one. Clearing that one and trying again would meet the next
        # having already taken something away, so what is in the way is counted
        # in full first and removed only if all of it is ours to remove.
        await _make_room(store, ledger, package)
        try:
            imported = await _attempt(store, package, origin, recorded)
        except CatalogImportConflict as still:
            # Everything nameable was removed and something answers to the
            # package's name anyway. Not the package's fault, and not something
            # its owner can edit their way out of.
            raise HTTPException(
                status_code=500,
                detail=f"Something is still in the way after replacing: {still}",
            ) from still

    # The manifest as checked and written, not as it arrived — what the panel is
    # told about should be what the catalog now holds.
    manifest = imported.manifest
    return {
        "id": manifest.id,
        "name": manifest.name or manifest.id,
        "digest": package.canonical_digest,
        # False is not a doubt about the package — it is that nobody offered a
        # value to check it against. Folding the two together would let "we
        # compared this with nothing" reach the panel reading as "verified".
        "digest_confirmed": expected_digest is not None,
        "steps_checked": len(package.steps()),
        # The catalog reaches the store once, at startup: there is no watcher and
        # no scan loop. An item that only got as far as disk would not be
        # openable until the next restart. Awaited bare, unlike the two calls
        # above: the loader hands its own file work to a worker thread.
        "project_id": await load_house(store, ledger, imported.item_dir),
    }


async def _attempt(
    store: ScopedStore, package: ClsPackage, origin: str, recorded: Mapping[str, Any] | None = None
) -> ImportResult:
    """One go at writing the package, with every refusal but a conflict answered.

    A conflict is left to travel: it is the only one whose remedy is not the
    caller's to give, because replacing what is in the way is a decision that
    belongs to whoever owns the copy. Everything else is already an answer, and
    has to be, since a replacement runs this twice.

    The caller holds the import gate. This does not take it.
    """
    # What is loaded, not what is on disk, is what decides a takeover — and it
    # is read again on a second attempt, because the first thing a replacement
    # does is change it.
    already_loaded = set((await store.catalog_item_ids()).values())
    try:
        # Running the gate over each step and writing the item is more filesystem
        # work than an event loop should do inline — the api service is answering
        # other requests, the worker's among them.
        return await asyncio.to_thread(_take_in, package, origin, already_loaded, recorded)
    except CatalogImportConflict:
        raise
    except CatalogImportUnavailable as exc:
        # Not the package's fault and not something its owner can edit their way
        # out of, so it must not be reported in the same breath as one that is.
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except CatalogImportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


async def _make_room(store: ScopedStore, ledger: Ledger, package: ClsPackage) -> None:
    """Take away everything standing where a replacement is about to be written.

    Counted in full before any of it goes. More than one thing can answer to a
    package's name — an item fetched earlier and a bundled sample sharing an id,
    or two items whose ids fold to the same directory — and the import reports
    only the first it meets. Removing that one and trying again would meet the
    next with the first already gone, which is how someone ends up with neither
    the copy they had nor the one they asked for.

    Only items that arrived as packages. The bundled catalog ships with the
    image and the deployment mounts it read-only: it is a product asset, not
    somewhere downloads accumulate, and a download that could quietly replace
    part of it would make the shipped content something a remote server decides.
    One bundled item in the way is enough to refuse the whole replacement, with
    nothing removed.

    The caller holds the import gate. This does not take it — `DELETE /catalog`
    does, and reusing that path here would deadlock.
    """
    already_loaded = set((await store.catalog_item_ids()).values())
    in_the_way = await asyncio.to_thread(occupants_of, package, already_loaded)

    bundled = [seen for seen in in_the_way if seen.is_bundled()]
    if bundled:
        named = ", ".join(repr(seen.occupant) for seen in bundled)
        raise HTTPException(
            status_code=403,
            detail=(
                f"{named} did not arrive here as a package, so it is not "
                "something a download may replace. Nothing was removed."
            ),
        )

    for seen in in_the_way:
        try:
            if seen.where is None:
                # A project answering to an id no directory claims. Nothing to
                # delete; the record is what stands in the way.
                await clear_house(store, ledger, seen.occupant)
            elif not await remove_imported_house(store, ledger, seen.occupant):
                # A directory of ours that no id answers to — a manifest this
                # build can no longer read still occupies the place it sits in.
                # There is no id to remove it by, and leaving it would block
                # every future fetch of that name with no way to clear it.
                await asyncio.to_thread(discard_item_dir, seen.where)
        except OSError as exc:
            raise HTTPException(
                status_code=500, detail=f"Could not remove {seen.occupant!r} to replace it."
            ) from exc


def _take_in(
    package: ClsPackage,
    origin: str,
    already_loaded: set[str],
    recorded: Mapping[str, Any] | None = None,
) -> ImportResult:
    """Import, refusing to take over an id the store already holds.

    ``already_loaded`` comes from the db rather than the ledger: what
    decides a takeover is whether a project answers to that catalog id, and the
    project rows are that, where the sidecar file was only a record of it. It is
    resolved before this crosses to the worker thread, since the query is async.
    """
    return import_package(package, origin=origin, already_loaded=already_loaded, recorded=recorded)


def _require_package_type(header: str) -> None:
    """Refuse a body a browser would post without asking permission first.

    The three types a form can be submitted as — `text/plain`,
    `application/x-www-form-urlencoded`, `multipart/form-data` — are the ones a
    page may send cross-origin with no preflight, so an endpoint that accepts
    one of them accepts a request from any site the user happens to visit. Every
    other write endpoint here takes a JSON body and gets that protection without
    asking; this one reads the body raw, so it has to ask.
    """
    kind = header.split(";", 1)[0].strip().lower()
    if kind not in PACKAGE_CONTENT_TYPES:
        raise HTTPException(
            status_code=415,
            detail="Send the package as application/octet-stream.",
        )


@asynccontextmanager
async def _admitted(request: Request) -> AsyncIterator[None]:
    """A turn at receiving a package, or a refusal to take this one now.

    Taken before the read rather than after it, which is the whole point. A
    limit reached once the body is here has already let the memory be spent —
    that is what `import_gate` is placed to do, and it is placed there for a
    different reason. A request waiting on this one has not been read from at
    all, so what it occupies is a connection.

    The place is held for the whole import, not only the receiving: the caller
    wraps writing to the catalog in this too, so a turn covers the read of the
    archive and the wait on `import_gate` as well. Only the receiving has a
    clock (`IMPORT_BODY_TIMEOUT`); what follows it is bounded by being finite
    work on a package the size limit has already capped.

    The semaphore is made on first use and kept on the app, because a process
    can build more than one app and the tests build one per case. That is not
    the same as being safe across event loops — one of these binds to the first
    loop it ever has to *wait* on, so an app served by two loops could still
    meet that, invisibly until the first time it contends. It is not built in
    the application factory because that imports its routers optionally, and
    reaching back here for the size would quietly make this module required.
    """
    admissions = getattr(request.app.state, "import_admissions", None)
    if admissions is None:
        # No await between the look and the set, so two requests arriving
        # together cannot each make one.
        admissions = asyncio.Semaphore(MAX_CONCURRENT_IMPORTS)
        request.app.state.import_admissions = admissions

    if not admissions.locked():
        # Taken without waiting, and asked for separately because `wait_for` is
        # not a way to express "do not wait": with no time left it cancels the
        # acquire before that has had a chance to run, so a deployment setting
        # the wait to zero would refuse every request to an idle machine.
        await admissions.acquire()
    else:
        try:
            await asyncio.wait_for(admissions.acquire(), IMPORT_ADMISSION_TIMEOUT)
        except TimeoutError:
            raise HTTPException(
                status_code=503,
                detail="This machine is already taking as many packages as it will hold at once.",
                headers={"Retry-After": str(max(1, round(IMPORT_ADMISSION_TIMEOUT)))},
            ) from None

    try:
        yield
    finally:
        admissions.release()


async def _received(request: Request) -> tempfile.SpooledTemporaryFile:
    """The request body, refused while it is still arriving if it is too large.

    Counted as it comes rather than measured once it is here: a limit applied
    after the read has already spent what it was there to protect, and a
    declared ``Content-Length`` is a number the sender chose.

    Written to a file rather than joined into one buffer at the end. Joining
    holds the pieces and the finished thing at the same time, so the moment of
    joining costs twice what arrived — over a route that takes no credentials,
    and with nothing bounding how many bodies are in this state at once. The
    reader takes an open file, so nothing downstream needs the bytes together.

    The caller closes what this returns. The threshold below is what a package
    has to exceed before any of it reaches the disk at all; under it this is an
    in-memory buffer that grows once rather than twice.

    The whole read is on a clock, because the caller holds a place in the
    admission limit for as long as it lasts. Without one, as many connections as
    there are places, sending headers and then trickling — or stopping — hold
    every place there is for as long as they care to, and nothing else may
    import at all. The limit ahead of this is what makes that worth attacking,
    so the two belong together.

    A deadline over the whole body rather than a gap between chunks: a sender
    that answers just before each gap expires never trips an idle timer, and
    holding a place is what it is after.
    """
    # Left to pick its own location on purpose. What that buys, once this has
    # rolled over, is a file created 0600 and unlinked before a byte is written
    # to it: it never has a name for anything else to reach, and the space comes
    # back even if this process dies badly. Passing `dir=` or `delete=False`
    # gives that up — this holds a body nobody signed in to send.
    spool = tempfile.SpooledTemporaryFile(max_size=SPOOL_THRESHOLD_BYTES)
    received = 0
    try:
        async with asyncio.timeout(IMPORT_BODY_TIMEOUT):
            async for chunk in request.stream():
                received += len(chunk)
                if received > MAX_IMPORT_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"A package may not be larger than {MAX_IMPORT_BYTES} bytes.",
                    )
                # Handed to a worker once this has rolled over to disk, where the
                # write is real work that an event loop answering other requests
                # should not be doing inline.
                await asyncio.to_thread(spool.write, chunk)
        # Inside the guard below, not after it: once the body has rolled over
        # this is a real seek, and a filesystem that failed it would otherwise
        # be the one way out of this function that leaves a file behind.
        spool.seek(0)
    except TimeoutError:
        spool.close()
        raise HTTPException(
            status_code=408,
            detail=f"A package must finish arriving within {IMPORT_BODY_TIMEOUT:g} seconds.",
        ) from None
    except BaseException:
        # A refusal here, or a client that goes away mid-body, must not leave a
        # temporary file behind: nothing downstream ever learns this existed.
        spool.close()
        raise
    return spool


def _origin(filename: str) -> str:
    """Where an imported item came from, in the words its provenance records.

    A label, never a path: the directory the item lands in is built from the
    manifest's id, and nothing said here is joined onto anywhere.
    """
    label = label_for(filename)
    return f"the file {label}" if label else "a file on this machine"


def label_for(text: str) -> str:
    """Someone else's text, trimmed to something safe to record and show.

    One definition rather than one per origin: this is the sanitising step, and
    two copies of it are two things to keep in step.
    """
    return _UNPRINTABLE.sub("", text).strip()[:MAX_ORIGIN_LENGTH]
