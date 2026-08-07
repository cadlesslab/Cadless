"""Sidecar ledger holding what the catalog needs to *show* an item.

Each entry carries an item's display metadata (name, domain, category, tags,
description, whether it baked a thumbnail), where it came from, and the content
hash that makes loading incremental.

What it no longer decides is whether a project is read-only. That answer moved
onto the project row as ``projects.catalog_item_id``, because a file
beside the db is a separate thing that can go bad on its own: every route that
can mutate a project asked this file first, so one unreadable copy of it turned
ordinary projects' writes — and the project list, and chat — into 500s, and
treating it as empty instead would have re-imported the whole catalog on the next
start. The db answers both of those now. This file is read by the catalog pages,
and when it cannot be read only they are affected.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import IO

from cadless.config import Settings, settings

try:  # POSIX only, like the worker's rlimits — see `cadless/worker.py`.
    import fcntl
except ImportError:  # pragma: no cover - this project ships and tests on Linux
    fcntl = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


class LedgerUnreadable(RuntimeError):
    """The ledger file exists but does not parse into a ledger.

    Raised rather than resolved to ``{}`` so no caller mistakes "cannot tell"
    for "nothing is loaded" — the two answers lead to opposite behavior, and the
    quiet one loses catalog items. Callers that can carry on say so explicitly;
    :meth:`Ledger.quarantine` is how the startup path clears the way.
    """


class LedgerBusy(RuntimeError):
    """Another process was mid-write and did not let go in time.

    Separate from :class:`LedgerUnreadable` because the file is fine and the
    answer is "not now" rather than "not ever". What a caller does about it is
    the same either way, and so is the cost: a recording that is dropped stays
    dropped until somebody runs ``catalog reload``, because a project row that
    says "already loaded" with no entry beside it is what `load_house` skips.
    Dropping it is still the better answer than failing the load, which would
    leave the item not loaded at all.
    """


# How long a writer waits for one in another process. Short on purpose: a write
# happens with the import gate held, so waiting here holds up every import and
# every catalog delete on this machine, for as long as some CLI run cares to
# take. Two seconds is longer than a write of this file could honestly need and
# short enough that a stuck holder costs one entry rather than the endpoint.
LOCK_WAIT = 2.0

# Set the first time a mount turns out to have no locking, so the report of it
# is made once rather than on every write.
_LOCKING_UNAVAILABLE = False


# Recording a load is a read-modify-write of the whole file, and the loader runs
# it in a worker thread so the event loop is free to answer other requests. That
# move costs the accidental safety of running inline on the loop, where nothing
# could interleave: two imports arriving together would each write back only
# what they read at the start. This serialises the writers, and only them.
# Readers go without it and are whole anyway, because `_write` swaps a finished
# file into place instead of rewriting the live one: a reader sees the ledger
# either before a write or after it, never during. Putting them under the lock
# as well would buy nothing and would leave every reader queued behind whichever
# writer holds it. This one orders the threads of one process; `_across_processes`
# below orders the processes, and writers hold both.
_FILE_LOCK = threading.Lock()


@contextmanager
def _across_processes(lock_path: Path, timeout: float) -> Iterator[None]:
    """Exclusive against writers in other processes, or give up saying so.

    A CLI run inside the api container is the documented way to load the catalog
    (`cadless/catalog/README.md`), so "one process at a time" is not an
    assumption this file may make. Two of them in a read-modify-write both read
    the ledger they are about to replace, and the one that finishes last puts
    back a copy that never had the other's entry — or, worse, takes the staged
    file the other was about to move and dies on a path nothing holds.

    The lock is a file of its own rather than the ledger. `_write` finishes with
    `os.replace` and `quarantine` moves the ledger aside, and a lock taken on an
    open file follows that file, not the name: after either of those, two
    processes holding "the ledger" would be holding different things. Nothing
    ever replaces this one.

    Waiting is bounded and refusing is the answer, not blocking. Writers run
    with the import gate held, so an unbounded wait here is a way for any
    process on the machine to stop every import and every catalog delete.

    Where the filesystem has no locking to offer, the write goes ahead without
    it. That is exactly what this code did before the lock existed, and it is
    the only answer that does not turn a working deployment on such a mount into
    a failing one over an optimisation.
    """
    if fcntl is None:  # pragma: no cover - this project ships and tests on Linux
        yield
        return
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    # Opened append-only and never written to: the mode creates the file without
    # truncating a holder's, and nothing needs its contents. The descriptor is
    # what carries the lock, so it stays open for the whole critical section.
    with open(lock_path, "a") as handle:
        if not _held(handle, lock_path, timeout):
            yield
            return
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _held(handle: IO[str], lock_path: Path, timeout: float) -> bool:
    """Take the lock, or say the filesystem does not have one to take.

    Contention and "no locking here" arrive as different exceptions and mean
    opposite things, so they are separated rather than both read as busy: a
    mount without `flock` would otherwise spend the whole timeout spinning and
    then report a holder that was never there.
    """
    deadline = time.monotonic() + timeout
    while True:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            # Someone has it. The only case worth waiting on, and only this long.
            if time.monotonic() >= deadline:
                raise LedgerBusy(
                    f"{lock_path} was held by another process for {timeout:g}s"
                ) from None
            time.sleep(0.02)
        except OSError:
            # ENOLCK and friends: the lock cannot be taken here by anyone, so
            # waiting would never end and refusing would cost the entry every
            # time. Logged once, since it is a property of the mount rather than
            # of this write.
            global _LOCKING_UNAVAILABLE
            if not _LOCKING_UNAVAILABLE:
                _LOCKING_UNAVAILABLE = True
                logger.exception(
                    "no file locking at %s; catalog ledger writes are not ordered "
                    "across processes on this filesystem",
                    lock_path,
                )
            return False


class Ledger:
    """A tiny JSON map: ``house_id -> {step_count, loaded_at, ...display fields}``."""

    def __init__(self, path: Path):
        self.path = Path(path)
        # Beside the ledger, never replaced, so a lock on it means the same
        # thing to two processes across a write or a quarantine.
        self.lock_path = self.path.with_name(self.path.name + ".lock")

    def _read(self) -> dict:
        """The ledger, or ``{}`` when there is not one yet.

        A file that is there but unparseable is not the same as no file, so it
        raises instead of coming back empty. Reading stays free of side effects —
        clearing a bad file is :meth:`quarantine`, which the startup path calls
        once and logs, rather than something a request does behind its own back.
        """
        if not self.path.exists():
            return {}
        text = self.path.read_text().strip()
        if not text:
            return {}
        try:
            data = json.loads(text)
        except ValueError as exc:  # JSONDecodeError
            raise LedgerUnreadable(f"{self.path} is not valid JSON: {exc}") from exc
        if not isinstance(data, dict):
            # Anything else (a list, a bare string) would make `.values()` or
            # `.get` fail somewhere further away from the cause than here.
            raise LedgerUnreadable(f"{self.path} holds {type(data).__name__}, not a ledger")
        return data

    def quarantine(self) -> Path | None:
        """Move an unusable ledger aside so a fresh one can be written.

        The bad copy is kept rather than deleted: it is the only evidence of what
        happened, and the catalog metadata in it may be worth reading by hand.
        Returns where it went, or ``None`` if there was no file to move.
        """
        if not self.path.exists():
            return None
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        aside = self.path.with_name(f"{self.path.name}.corrupt-{stamp}")
        os.replace(self.path, aside)
        logger.error(
            "catalog ledger at %s could not be read; moved to %s. Catalog listings "
            "will be missing item details until the catalog is loaded again.",
            self.path,
            aside,
        )
        return aside

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Build the next ledger beside the live one and move it over in a single
        # step. Readers take no lock — several of them run on the event loop —
        # so rewriting the live file in place would hand one an emptied ledger,
        # and for that moment every catalog project would read as an ordinary
        # editable one. This also survives a crash mid-write: the live file is
        # either the previous ledger or the next, never half of either.
        #
        # Named per process. The lock above orders writers wherever there is
        # locking to be had, but a mount without it — and any platform without
        # `fcntl` — falls back to writing unordered, and one shared name there is
        # worse than a lost entry: the first `os.replace` takes the file the
        # second is about to move, and that one dies on a path nothing holds,
        # inside a load nothing is expecting to fail.
        staged = self.path.with_name(f"{self.path.name}.writing.{os.getpid()}")
        staged.write_text(json.dumps(data, indent=2))
        os.replace(staged, self.path)

    def get(self, house_id: str) -> dict | None:
        return self._read().get(house_id)

    def record(
        self,
        house_id: str,
        step_count: int,
        *,
        name: str | None = None,
        domain: str | None = None,
        category: str | None = None,
        tags: list[str] | None = None,
        description: str | None = None,
        thumbnail: bool | None = None,
        source: dict | None = None,
        content_hash: str | None = None,
    ) -> None:
        """Record what the catalog panel shows about one item.

        Which project the item became is deliberately absent: that is
        ``projects.catalog_item_id`` and only there, because two records of one
        fact can disagree, and the one in this file is the one that can be lost
        or half-written. Entries written by older versions still carry a
        ``project_id`` — nothing reads it.
        """
        with _FILE_LOCK, _across_processes(self.lock_path, LOCK_WAIT):
            data = self._read()
            entry: dict = {
                "step_count": step_count,
                "loaded_at": datetime.now(UTC).isoformat(),
            }
            # Optional keys are written only when provided, so legacy entries (and
            # legacy callers) keep their exact shape (#21).
            # ``source`` surfaces the item's provenance record (source.json, #23);
            # ``content_hash`` makes ``load`` incremental (skip-unchanged, #23).
            optional = {
                "name": name,
                "domain": domain,
                "category": category,
                "tags": tags,
                "description": description,
                "thumbnail": thumbnail,
                "source": source,
                "content_hash": content_hash,
            }
            entry.update({k: v for k, v in optional.items() if v is not None})
            data[house_id] = entry
            self._write(data)

    def remove(self, house_id: str) -> None:
        with _FILE_LOCK, _across_processes(self.lock_path, LOCK_WAIT):
            data = self._read()
            if house_id in data:
                del data[house_id]
                self._write(data)

    def entries(self) -> dict:
        return self._read()


def default_ledger(config: Settings | None = None) -> Ledger:
    """Build the ledger at ``<data_dir>/catalog-ledger.json`` from settings."""
    cfg = config or settings
    return Ledger(Path(cfg.data_dir) / "catalog-ledger.json")
