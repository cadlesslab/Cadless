"""Give a catalog version of several parts the motion its viewer plays.

A catalog item is loaded with its files baked and nothing built, and the motion
the viewport plays -- which part comes off first, and which way -- is written
only when a build's parts are judged. So a version of several parts arrives with
none and is drawn whole, with nothing to play. Here each such version's step
code is built once, with no model call; its parts are judged as a build that
asked for no printer is judged; and the motion that build wrote is filed on the
version.

Once, in the background after startup, and a build at a time: the app answers
while it runs. Only items the deployment ships are built. A received item -- a
package somebody sent, imported through a route that asks for no sign-in -- is
left as it was: building it here would run code nobody asked to run, as often as
anyone cared to send it.

A version is left as it was wherever the result might describe something other
than what was baked -- the gate refuses its code, the build fails or its parts
cannot be ordered, it orders another number of parts than the version draws, or
it measures another size than the version filed. A motion is filed for good, and
a version without one is drawn exactly as it was before.

The store is widened to the build here, as the catalogue loader widens it: a
catalog item is the installation's, so its rows are written as the
installation's. The store is the one the app was built with; no route reaches
this module.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import shutil
import uuid
from collections.abc import Callable
from pathlib import Path

from backend import artifact_io
from backend.catalog_state import ledger_for
from cadless.catalog.domains import find_domain
from cadless.catalog.importer import imported_item_dir
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

#: How much of a build's error a log line carries. The text is the script's to
#: choose, so it is quoted rather than written out, and cut short.
_SAID = 300


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
        return f"its build failed: {rb.error!r:.{_SAID}}"
    verdict = rb.assembly or {}
    order = verdict.get("order") or []
    if not verdict.get("ok") or len(order) < 2:
        failures = "; ".join(verdict.get("failures") or []) or "nothing was measured"
        return f"its parts could not be ordered: {failures!r:.{_SAID}}"
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


async def _built(run: Callable[..., Rebuild], code: str, staging: Path, scale: float) -> Rebuild:
    """``run`` on a thread, and on a cancellation the build's end waited for.

    The build carries on in its thread whatever happens to the task waiting on
    it, and writes into the staging directory as it finishes; removing that
    directory before then would leave what it writes afterwards with nothing to
    remove it.
    """
    building = asyncio.ensure_future(
        asyncio.to_thread(
            run,
            code,
            export_dir=str(staging),
            describe=False,
            export_scale=scale,
            assembly_clock=True,
        )
    )
    try:
        return await asyncio.shield(building)
    except asyncio.CancelledError:
        with contextlib.suppress(Exception):
            await building
        raise


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
    label = f"{item_id!r} (version {version.id})"
    code = version.code or ""
    # On a thread: parsing a long script holds the event loop for as long as it takes.
    gated = await asyncio.to_thread(validate_code, code)
    if not gated.ok:
        logger.warning(
            "catalog motion: %s not built: the gate refuses its code: %.*r",
            label,
            _SAID,
            "; ".join(gated.reasons),
        )
        return False
    staging = Path(build.artifacts_dir) / "_staging" / uuid.uuid4().hex
    try:
        await asyncio.to_thread(staging.mkdir, parents=True)
        rb = await _built(run, code, staging, scale)
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
    gate: asyncio.Lock | None = None,
    run: Callable[..., Rebuild] | None = None,
) -> int:
    """File a motion on each shipped catalog version of several parts that has none; how many.

    ``gate`` is held while a motion is filed. ``run`` stands in for
    :func:`cadless.pipeline.rebuild`.

    An item is built at the scale its domain's units call for, read from the
    ledger, which is the one place the domain is kept. An item whose domain cannot
    be read is not built at all: a build at the wrong scale orders parts against
    clearances a thousand times off.
    """
    build = system_view(store)
    run = run or rebuild
    catalog = await build.catalog_item_ids()
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
        if await asyncio.to_thread(imported_item_dir, item_id) is not None:
            continue
        entry = entries.get(item_id)
        key = entry.get("domain") if isinstance(entry, dict) else None
        domain = find_domain(key) if isinstance(key, str) else None
        if domain is None:
            logger.warning(
                "catalog motion: %r names no units this engine knows; not built", item_id
            )
            continue
        for version in await build.list_versions(project_id):
            filed += await _fill(build, item_id, version, domain.export_scale, gate, run)
    return filed


class CatalogMotions:
    """The startup pass that fills catalog motions, as one background task."""

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
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        """Begin the pass and return at once. Called from the running event loop."""
        self._task = asyncio.get_running_loop().create_task(self._pass())

    async def idle(self) -> None:
        """Wait until the pass has finished, if one was started."""
        if self._task is not None:
            await asyncio.shield(self._task)

    async def close(self) -> None:
        """Stop the pass. A build in hand is let finish, and nothing is filed from it."""
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _pass(self) -> None:
        try:
            await fill_motions(self._store, ledger_for(self._store), gate=self._gate, run=self._run)
        except Exception:
            logger.exception("catalog motion: the pass stopped")
