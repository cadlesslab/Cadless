"""Copying a build's exports into a version's artifact directory.

One funnel, and deliberately so. A part's ordinal is assigned by the store as it
writes the row, so **the order the rows are written is the numbering**. Every
call site deciding that order for itself is another chance for a part to be filed
under a number its filename does not match -- a mismatch nothing raises on,
because both halves are individually well formed.

The build writes its files and this reads them back; the naming contract they
share lives in :mod:`cadless.exporters`, next to the writer.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from cadless.exporters import EXPORTERS, part_index
from cadless.scoped_store import AnyStore

#: The kind guide frames are filed under. Deliberately **not** an ``EXPORTERS``
#: member: a guide is not a format the model was exported to, and adding it there
#: would put it through :func:`exported_parts`, whose ``model_p*`` naming is a
#: per-part contract a guide does not meet -- so the frames would be written and
#: then silently not found. It reaches a reader by a media type on the download
#: route and nothing else.
GUIDE_KIND = "guide"

#: ``guide_f0.png``, ``guide_f1.png``. Numbered like the parts and parsed the same
#: way, for the same reason: the order these are read in is the ordinal each is
#: filed under, and a lexicographic sort would file frame ten as frame one.
_GUIDE_STEM = re.compile(r"^guide_f(\d+)$")


def exported_parts(src_dir: Path, kind: str) -> list[Path]:
    """Every file of ``kind`` this build wrote, in part order.

    Ordered on the number parsed out of the name, never on the name itself: as
    text ``model_p10`` sorts before ``model_p2``, and since this order decides the
    ordinal each file is filed under, a lexicographic sort would quietly file part
    ten as part one. A file whose stem carries no number is skipped rather than
    guessed at.

    A one-solid build wrote ``model.{kind}`` and is returned as the single part it
    is. Finding the plain name settles the question, so the two namings sharing a
    directory would be a build written on top of another's leftovers -- which the
    export step is responsible for not leaving. What this does in that case is
    pinned by a test rather than left to the reader.
    """
    single = src_dir / f"model.{kind}"
    if single.exists():
        return [single]
    numbered: list[tuple[int, Path]] = []
    for path in src_dir.glob(f"model_p*.{kind}"):
        index = part_index(path.stem)
        if index is not None:
            numbered.append((index, path))
    return [path for _, path in sorted(numbered)]


def guide_frames(src_dir: Path) -> list[Path]:
    """Every guide frame this build drew, in frame order."""
    numbered: list[tuple[int, Path]] = []
    for path in Path(src_dir).glob("guide_f*.png"):
        found = _GUIDE_STEM.match(path.stem)
        if found is not None:
            numbered.append((int(found.group(1)), path))
    return [path for _, path in sorted(numbered)]


async def copy_and_register(store: AnyStore, version_id: int, src_dir: str | Path) -> int:
    """Copy every exported part in, register each, and return how many were written.

    The filename is carried across unchanged, so what is on disk under the version
    is what the build called it, and the part ordinal the row receives is the
    position in the order above.

    ``store`` is annotated rather than left bare because this is the one place
    every artifact write now passes through, and with nothing said a later caller
    would have nothing to read. It is ``AnyStore`` rather than the scoped view
    alone because that is what the function accepts and what its own test hands
    it; what keeps a *route* on the scoped view is the import check in
    ``tests/test_store_surface.py``, which covers this module by name.
    """
    src = Path(src_dir)
    dest = Path(store.version_artifact_dir(version_id))
    written = 0
    for kind in EXPORTERS:
        for path in exported_parts(src, kind):
            target = dest / path.name
            shutil.copy(path, target)
            await store.add_artifact(version_id, kind, str(target))
            written += 1
    # The guide's frames go through the same funnel, for the reason the funnel
    # exists: their ordinals are assigned by the order they are written in, and a
    # second call site deciding that order is how a frame ends up filed under a
    # number its name does not match.
    for path in guide_frames(src):
        target = dest / path.name
        shutil.copy(path, target)
        await store.add_artifact(version_id, GUIDE_KIND, str(target))
        written += 1
    return written
