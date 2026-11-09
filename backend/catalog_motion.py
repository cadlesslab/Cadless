"""Give a catalog version of several parts the motion its viewer plays.

A catalog item is loaded with its files baked and nothing checked as an
assembly, and the motion the viewport plays -- which part comes off first, and
which way -- is written only when a build's parts are judged. So a version of
several parts arrives with none and is drawn whole, with nothing to play. Here
each such version's step code is built with no model call, at every start until
it has a motion; its parts are judged as a build that asked for no printer is
judged; and the motion that build wrote is filed on the version.

In the background and a build at a time: the app answers while it runs. Only
items the deployment ships are built. A received item -- a package somebody
sent, imported through a route that asks for no sign-in -- is left as it was:
building it here would run code nobody asked to run, as often as anyone cared to
send it.

A version is left as it was wherever the result might describe something other
than what was baked -- the gate refuses its code, the build fails or its parts
cannot be ordered, it orders another number of parts than the version draws, or
one of its parts lies elsewhere than the baked part of that number. A motion is
filed for good, and a version without one is drawn exactly as it was before.

The store is widened to the build here, as the catalogue loader widens it: a
catalog item is the installation's, so its rows are written as the
installation's. Only the app's own store is taken; a route's scoped view is
refused rather than widened.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import shutil
import uuid
from collections.abc import Callable
from pathlib import Path

import numpy as np

from backend import artifact_io
from backend.catalog_state import authoring_units
from cadless.catalog.domains import MM_PER_UNIT
from cadless.catalog.importer import imported_catalog_root, scan_root, scan_startup_catalog
from cadless.catalog.thumbnail import load_mesh
from cadless.exporters import exported_parts
from cadless.pipeline import Rebuild, rebuild
from cadless.scoped_store import BuildStore, ScopedStore, system_view
from cadless.store import Artifact, ScriptVersion, Store
from cadless.validation import validate_code

logger = logging.getLogger(__name__)

#: How far, in millimetres, a rebuilt part's box may stray from the baked part's
#: before it is taken for another part. Both are meshes the exporter wrote from
#: the same code, so they differ only where two tessellations of a curve put an
#: extreme vertex in slightly different places -- a small fraction of this. Two
#: parts that traded numbers lie apart by far more.
PART_TOLERANCE_MM = 0.5

#: How much of a build's error a log line carries. The text is the script's to
#: choose, so it is quoted rather than written out, and cut short.
_SAID = 300


def _parts(artifacts: list[Artifact], kind: str) -> list[Artifact]:
    """The version's rows of one kind, in part order."""
    return sorted((a for a in artifacts if a.kind == kind), key=lambda a: a.part)


def _has_motion(artifacts: list[Artifact]) -> bool:
    return any(artifact.kind == artifact_io.ASSEMBLY_KIND for artifact in artifacts)


def _why_unordered(rb: Rebuild) -> str:
    """What the build said about why its parts have no order, from the measurement too.

    Judged with the parts checks alone, a search that found no order, or ran out of
    time looking, leaves nothing in the verdict's own lists; the measurement keeps it.
    """
    verdict = rb.assembly or {}
    said = [*(verdict.get("failures") or []), *(verdict.get("unchecked") or [])]
    measured = rb.result.assembly
    if measured is not None:
        if measured.trapped:
            said.append(f"parts {sorted(measured.trapped)} could not be freed")
        said.extend(measured.unchecked)
    return "; ".join(said) or "nothing was measured"


def _box(path: Path) -> np.ndarray | None:
    """A mesh's lowest and highest corner, as six numbers, or ``None`` for no mesh."""
    triangles = load_mesh(path)
    if triangles.size == 0:
        return None
    points = triangles.reshape(-1, 3)
    return np.concatenate([points.min(axis=0), points.max(axis=0)])


def _parts_unlike(staging: Path, baked: list[Path]) -> str | None:
    """Why the parts the build exported are not the baked ones, part by part, or ``None``.

    The motion moves the version's own part files by number, so part ``i`` of the
    build has to be part ``i`` of what was baked: a count and an overall size
    cannot tell two parts that traded places from the parts in theirs.
    """
    built = exported_parts(staging, "stl")
    if len(built) != len(baked):
        return f"its build exported {len(built)} parts where {len(baked)} were baked"
    for index, (new, old) in enumerate(zip(built, baked, strict=True)):
        here, there = _box(new), _box(old)
        if here is None or there is None:
            return f"part {index} has no mesh to hold its build to"
        if not np.allclose(here, there, rtol=0.0, atol=PART_TOLERANCE_MM):
            spans = np.round(here, 2).tolist()
            baked_spans = np.round(there, 2).tolist()
            return f"its part {index} spans {spans} mm where the baked part {index} spans {baked_spans}"
    return None


def _unlike_what_was_baked(rb: Rebuild, parts: int) -> str | None:
    """Why the motion ``rb`` wrote would not describe the version, or ``None``.

    The viewer plays a motion only when its order names exactly the parts it
    draws, so a build that ordered another number is refused here rather than
    filed and then never played.
    """
    if not rb.ok:
        return f"its build failed: {rb.error!r:.{_SAID}}"
    verdict = rb.assembly or {}
    order = verdict.get("order") or []
    if not verdict.get("ok") or len(order) < 2:
        return f"its parts could not be ordered: {_why_unordered(rb)!r:.{_SAID}}"
    if len(order) != parts:
        return f"its build ordered {len(order)} parts where the version draws {parts}"
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
        asyncio.to_thread(run, code, export_dir=str(staging), describe=False, export_scale=scale)
    )
    try:
        return await asyncio.shield(building)
    except asyncio.CancelledError:
        with contextlib.suppress(Exception):
            await building
        raise


async def _fill_version(
    build: BuildStore,
    label: str,
    version: ScriptVersion,
    scale: float,
    gate: asyncio.Lock | None,
    run: Callable[..., Rebuild],
) -> bool:
    """Build one version and file its motion; whether one was filed."""
    artifacts = await build.list_artifacts(version.id)
    drawn = _parts(artifacts, "glb")
    if len(drawn) < 2 or _has_motion(artifacts):
        return False
    baked = [Path(a.path) for a in _parts(artifacts, "stl")]
    if len(baked) != len(drawn):
        logger.warning(
            "catalog motion: %s not built: it has no part meshes to hold a build to", label
        )
        return False
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
        why = _unlike_what_was_baked(rb, len(drawn))
        if why is None:
            why = await asyncio.to_thread(_parts_unlike, staging, baked)
        if why is not None:
            logger.warning("catalog motion: none filed for %s: %s", label, why)
            return False
        # Held for the filing alone, so the version cannot go between the check
        # and the row; a build is far too long to hold it for.
        async with gate if gate is not None else contextlib.nullcontext():
            return await _file(build, version.id, staging)
    finally:
        await asyncio.to_thread(shutil.rmtree, staging, ignore_errors=True)


async def _fill(
    build: BuildStore,
    item_id: str,
    version: ScriptVersion,
    scale: float,
    gate: asyncio.Lock | None,
    run: Callable[..., Rebuild],
) -> bool:
    """:func:`_fill_version`, which never raises: one version costs only itself."""
    label = f"{item_id!r} (version {version.id})"
    try:
        filed = await _fill_version(build, label, version, scale, gate, run)
    except Exception:
        logger.exception("catalog motion: %s could not be built", label)
        return False
    if filed:
        logger.info("catalog motion: filed for %s", label)
    return filed


def _scans():
    return scan_startup_catalog(), scan_root(imported_catalog_root())


async def fill_motions(
    store: Store,
    *,
    gate: asyncio.Lock | None = None,
    run: Callable[..., Rebuild] | None = None,
) -> int:
    """File a motion on each shipped catalog version of several parts that has none; how many.

    ``gate`` is held while a motion is filed. ``run`` stands in for
    :func:`cadless.pipeline.rebuild`.

    Shipped is what the catalog root's walk saw and the received root does not
    hold. A walk of the received root that could not read everything builds
    nothing: it cannot say an item is not received. An item is built at the scale
    its units call for; an item whose units cannot be read is not built at all,
    since a build at the wrong scale orders parts against clearances a thousand
    times off.
    """
    if isinstance(store, ScopedStore):
        raise TypeError("fill_motions takes the app's own store, never a route's view")
    build = system_view(store)
    run = run or rebuild
    catalog = await build.catalog_item_ids()
    if not catalog:
        return 0
    shipped, received = await asyncio.to_thread(_scans)
    if not received.complete:
        logger.warning(
            "catalog motion: the received catalog could not be read in full, so a "
            "shipped item cannot be told from a received one; nothing built"
        )
        return 0
    filed = 0
    for project_id, item_id in sorted(catalog.items()):
        if not shipped.claims(item_id) or received.claims(item_id):
            continue
        try:
            project = await build.get_project(project_id)
            units = await authoring_units(build, project) if project is not None else None
            versions = await build.list_versions(project_id)
        except Exception:
            logger.exception("catalog motion: %r could not be read", item_id)
            continue
        if units not in MM_PER_UNIT:
            logger.warning(
                "catalog motion: %r names no units this engine knows; not built", item_id
            )
            continue
        for version in versions:
            filed += await _fill(build, item_id, version, MM_PER_UNIT[units], gate, run)
    return filed


class CatalogMotions:
    """The startup pass that fills catalog motions, as one background task."""

    def __init__(
        self,
        store: Store,
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
            await fill_motions(self._store, gate=self._gate, run=self._run)
        except Exception:
            logger.exception("catalog motion: the pass stopped")
