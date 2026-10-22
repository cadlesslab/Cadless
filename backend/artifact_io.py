"""Copying a build's exports into a version's artifact directory.

One funnel, and deliberately so. A part's ordinal is assigned by the store as it
writes the row, so **the order the rows are written is the numbering**. Every
call site deciding that order for itself is another chance for a part to be filed
under a number its filename does not match -- a mismatch nothing raises on,
because both halves are individually well formed.

The build writes its files and :func:`cadless.exporters.exported_parts` reads them
back; both the naming contract and that reader live next to the writer, and this
module re-exports it for the callers that have always reached it through here.
"""

from __future__ import annotations

import asyncio
import re
import shutil
from pathlib import Path

from cadless.assembly_guide import MOTION_FILE
from cadless.exporters import ASSEMBLY_FILES, EXPORTERS, exported_parts
from cadless.scoped_store import AnyStore

#: The kind guide frames are filed under. Deliberately **not** an ``EXPORTERS``
#: member: a guide is not a format the model was exported to, and adding it there
#: would put it through :func:`exported_parts`, whose ``model_p*`` naming is a
#: per-part contract a guide does not meet -- so the frames would be written and
#: then silently not found. It reaches a reader by a media type on the download
#: route and nothing else.
GUIDE_KIND = "guide"

#: The kind an assembly's motion is filed under: the measured order and headings
#: the viewport plays the parts apart and together from. Kept out of
#: ``EXPORTERS`` for the reason ``GUIDE_KIND`` is: it is not a format the model
#: was exported to, and one file for the whole build does not meet the per-part
#: naming :func:`exported_parts` reads -- so it would be written and then never
#: found. It reaches a reader on its own route.
ASSEMBLY_KIND = "assembly"

#: ``guide_f0.png``, ``guide_f1.png``. Numbered like the parts and parsed the same
#: way, for the same reason: the order these are read in is the ordinal each is
#: filed under, and a lexicographic sort would file frame ten as frame one.
_GUIDE_STEM = re.compile(r"^guide_f(\d+)$")


def guide_frames(src_dir: Path) -> list[Path]:
    """Every guide frame this build drew, in frame order."""
    numbered: list[tuple[int, Path]] = []
    for path in Path(src_dir).glob("guide_f*.png"):
        found = _GUIDE_STEM.match(path.stem)
        if found is not None:
            numbered.append((int(found.group(1)), path))
    return [path for _, path in sorted(numbered)]


def _copy_in(src: Path, dest: Path) -> list[tuple[str, Path]]:
    """Copy every file of the build into ``dest``; return ``(kind, target)`` in order.

    The order of the list is the order the rows are written in, so it is the
    numbering: per-part exports kind by kind, then the guide's frames, then the
    files of which a build has one.
    """
    copied: list[tuple[str, Path]] = []

    def take(kind: str, path: Path) -> None:
        target = dest / path.name
        shutil.copy(path, target)
        copied.append((kind, target))

    for kind in EXPORTERS:
        for path in exported_parts(src, kind):
            take(kind, path)
    # The guide's frames go through the same funnel, for the reason the funnel
    # exists: their ordinals are assigned by the order they are written in, and a
    # second call site deciding that order is how a frame ends up filed under a
    # number its name does not match.
    for path in guide_frames(src):
        take(GUIDE_KIND, path)
    # The motion through the same funnel too, as the one file of its kind: part
    # 0. A build that left none registers none, rather than a row for a file
    # that is not there.
    motion = src / MOTION_FILE
    if motion.is_file():
        take(ASSEMBLY_KIND, motion)
    # The whole build as one file of each, on the same terms as the motion.
    for kind, name in ASSEMBLY_FILES.items():
        whole = src / name
        if whole.is_file():
            take(kind, whole)
    return copied


async def copy_and_register(store: AnyStore, version_id: int, src_dir: str | Path) -> int:
    """Copy every exported part in, register each, and return how many were written.

    The filename is carried across unchanged, so what is on disk under the version
    is what the build called it, and the part ordinal the row receives is the
    position in the order ``_copy_in`` copies in.

    The copying runs on a thread, off the event loop; the rows are written after
    it, one at a time and in its order, because that order is the numbering.

    ``store`` is annotated rather than left bare because this is the one place
    every artifact write now passes through, and with nothing said a later caller
    would have nothing to read. It is ``AnyStore`` rather than the scoped view
    alone because that is what the function accepts and what its own test hands
    it; what keeps a *route* on the scoped view is the import check in
    ``tests/test_store_surface.py``, which covers this module by name.
    """
    dest = Path(store.version_artifact_dir(version_id))
    copied = await asyncio.to_thread(_copy_in, Path(src_dir), dest)
    for kind, target in copied:
        await store.add_artifact(version_id, kind, str(target))
    return len(copied)
