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

import shutil
from pathlib import Path

from cadless.exporters import EXPORTERS, part_index
from cadless.scoped_store import AnyStore


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
    return written
