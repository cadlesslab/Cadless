"""Give a catalog version of several parts the motion its viewer plays.

A catalog item is loaded with its files baked and nothing built, and the motion
the viewport plays -- which part comes off first, and which way -- is written
only when a build's parts are judged. So a version of several parts arrives with
none and is drawn whole, with nothing to play. Here each such version's step
code is built once, with no model call; its parts are judged as a build that
asked for no printer is judged; and the motion that build wrote is filed on the
version.

In the background and a build at a time, through one queue: the app answers
while it runs, and startup and an import cannot set two builds going at once.
A version is left as it was wherever the result might describe something other
than what was baked -- the gate refuses its code, the build fails or its parts
cannot be ordered, it orders another number of parts than the version draws, or
it measures another size than the version filed. A motion is filed for good, and
a version without one is drawn exactly as it was before.

The store is widened to the build here, as the catalogue loader widens it: a
catalog item is the installation's, so its rows are written as the
installation's. No route hands its own view in -- an import asks with a project
id, and the rows are reached through the store the app was built with.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import shutil
import uuid
from collections.abc import Callable, Iterable
from pathlib import Path

from backend import artifact_io
from backend.catalog_state import ledger_for
from cadless.catalog.domains import find_domain
from cadless.catalog.ledger import Ledger, LedgerUnreadable
from cadless.pipeline import Rebuild, rebuild
from cadless.scoped_store import AnyStore, BuildStore, system_view
from cadless.store import Artifact, ScriptVersion
from cadless.validation import validate_code

logger = logging.getLogger(__name__)

#: How far a rebuilt model's size may stray from the size its version filed, as a
#: fraction of it, before the build is taken for some other model. Both come from
#: the same code measured the same way and agree to many places; the margin is
#: for a kernel that rounds a box a little differently, not for a model that
#: changed.
SIZE_TOLERANCE = 1e-3


def _parts_drawn(artifacts: list[Artifact]) -> int:
    """How many parts the viewer draws the version as: its ``glb`` part numbers."""
    return len({artifact.part for artifact in artifacts if artifact.kind == "glb"})


def _has_motion(artifacts: list[Artifact]) -> bool:
    return any(artifact.kind == artifact_io.ASSEMBLY_KIND for artifact in artifacts)


def _unlike_what_was_baked(
    rb: Rebuild, parts: int, filed: tuple[float, float, float] | None, scale: float
) -> str | None:
    """Why the motion ``rb`` wrote would not describe the version, or ``None``.

    The viewer plays a motion only when its order names exactly the parts it
    draws, so a build that ordered another number is refused here rather than
    filed and then never played. The size is compared in millimetres, as the
    version filed it: the build measures in the units its code is written in.
    """
    if not rb.ok:
        return f"its build failed: {rb.error}"
    verdict = rb.assembly or {}
    order = verdict.get("order") or []
    if not verdict.get("ok") or len(order) < 2:
        failures = "; ".join(verdict.get("failures") or []) or "nothing was measured"
        return f"its parts could not be ordered: {failures}"
    if len(order) != parts:
        return f"its build ordered {len(order)} parts where the version draws {parts}"
    built = rb.result.bbox
    if built is None or filed is None:
        return "there is no size to hold its build to"
    measured = tuple(side * scale for side in built)
    if not all(
        math.isclose(side, kept, rel_tol=SIZE_TOLERANCE, abs_tol=1e-6)
        for side, kept in zip(measured, filed, strict=True)
    ):
        return f"its build measures {measured} mm where the version filed {tuple(filed)}"
    return None


async def _file(build: BuildStore, version_id: int, staging: Path) -> bool:
    """File the motion, unless the version went, or got one, while it was built."""
    if await build.get_version(version_id) is None:
        return False
    if _has_motion(await build.list_artifacts(version_id)):
        return False
    return await artifact_io.file_motion(build, version_id, staging)


async def _fill(
    build: BuildStore,
    item_id: str,
    version: ScriptVersion,
    scale: float,
    gate: asyncio.Lock | None,
    run: Callable[..., Rebuild],
) -> bool:
    """Build one version and file its motion; whether one was filed. Never raises.

    One version that cannot be built costs only itself: the rest of the item, and
    the items after it, are still worth their motions.
    """
    artifacts = await build.list_artifacts(version.id)
    parts = _parts_drawn(artifacts)
    if parts < 2 or _has_motion(artifacts):
        return False
    label = f"{item_id} (version {version.id})"
    code = version.code or ""
    gated = validate_code(code)
    if not gated.ok:
        logger.warning(
            "catalog motion: %s not built: the gate refuses its code: %s",
            label,
            "; ".join(gated.reasons),
        )
        return False
    staging = Path(build.artifacts_dir) / "_staging" / uuid.uuid4().hex
    try:
        await asyncio.to_thread(staging.mkdir, parents=True)
        rb = await asyncio.to_thread(
            run,
            code,
            export_dir=str(staging),
            describe=False,
            export_scale=scale,
            assembly_clock=True,
        )
        why = _unlike_what_was_baked(rb, parts, version.bbox, scale)
        if why is not None:
            logger.warning("catalog motion: none filed for %s: %s", label, why)
            return False
        # Held for the filing alone. A reload or a delete of the same item holds
        # it for its whole write, so the version cannot go between the check and
        # the row; a build is far too long to hold it for.
        async with gate if gate is not None else contextlib.nullcontext():
            filed = await _file(build, version.id, staging)
    except Exception:
        logger.exception("catalog motion: %s could not be built", label)
        return False
    finally:
        await asyncio.to_thread(shutil.rmtree, staging, ignore_errors=True)
    if filed:
        logger.info("catalog motion: filed for %s", label)
    return filed


async def fill_motions(
    store: AnyStore,
    ledger: Ledger,
    *,
    project_ids: Iterable[int] | None = None,
    gate: asyncio.Lock | None = None,
    run: Callable[..., Rebuild] | None = None,
) -> int:
    """File a motion on each catalog version of several parts that has none; how many.

    ``project_ids`` narrows the pass to those catalog projects, as an import asks
    for the item it brought in; ``None`` is every one. ``gate`` is held while a
    motion is filed. ``run`` stands in for :func:`cadless.pipeline.rebuild`.

    An item is built at the scale its domain's units call for, read from the
    ledger, which is the one place the domain is kept. An item whose domain cannot
    be read is not built at all: a build at the wrong scale orders parts against
    clearances a thousand times off.
    """
    build = system_view(store)
    run = run or rebuild
    asked = None if project_ids is None else set(project_ids)
    catalog = await build.catalog_item_ids()
    if asked is not None:
        catalog = {pid: item for pid, item in catalog.items() if pid in asked}
    if not catalog:
        return 0
    try:
        entries = await asyncio.to_thread(ledger.entries)
    except (LedgerUnreadable, OSError, ValueError):
        # ValueError: bytes that are not text fail before the JSON parse that turns
        # a bad file into LedgerUnreadable.
        logger.warning(
            "catalog motion: the catalog ledger cannot be read, so no item's units are "
            "known; nothing built",
            exc_info=True,
        )
        return 0
    filed = 0
    for project_id, item_id in sorted(catalog.items()):
        entry = entries.get(item_id)
        key = entry.get("domain") if isinstance(entry, dict) else None
        domain = find_domain(key) if isinstance(key, str) else None
        if domain is None:
            logger.warning(
                "catalog motion: %s names no units this engine knows; not built", item_id
            )
            continue
        for version in await build.list_versions(project_id):
            filed += await _fill(build, item_id, version, domain.export_scale, gate, run)
    return filed


class CatalogMotions:
    """The one queue catalog motions are filled from, a build at a time.

    Startup asks for every catalog item and an import for the item it brought in;
    both go through here, so the two cannot build at once. Asked before
    :meth:`start` or after :meth:`close`, it does nothing -- which is what a
    deployment that switched it off gets.
    """

    def __init__(
        self,
        store: AnyStore,
        *,
        gate: asyncio.Lock | None = None,
        run: Callable[..., Rebuild] | None = None,
    ) -> None:
        self._store = store
        self._gate = gate
        self._run = run
        self._queue: asyncio.Queue[frozenset[int] | None] | None = None
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        """Begin taking asks. Called from the running event loop."""
        # Made here rather than in the constructor: a queue belongs to the loop
        # that first waits on it, and an app is built before its loop runs.
        self._queue = asyncio.Queue()
        self._task = asyncio.get_running_loop().create_task(self._drain())

    def ask(self, project_ids: Iterable[int] | None = None) -> None:
        """Fill these catalog projects' motions, or every one's; returns at once."""
        if self._queue is None or self._task is None or self._task.done():
            return
        self._queue.put_nowait(None if project_ids is None else frozenset(project_ids))

    async def idle(self) -> None:
        """Wait until everything asked so far has been done."""
        if self._queue is not None:
            await self._queue.join()

    async def close(self) -> None:
        """Stop taking asks and abandon the one in hand. A build in hand finishes
        on its thread, and nothing is filed from it."""
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _drain(self) -> None:
        ledger = ledger_for(self._store)
        while True:
            asked = await self._queue.get()
            try:
                await fill_motions(
                    self._store, ledger, project_ids=asked, gate=self._gate, run=self._run
                )
            except Exception:
                logger.exception("catalog motion: a pass stopped")
            finally:
                self._queue.task_done()
