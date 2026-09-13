"""Copying a build's exports into a version's artifact directory.

One funnel, and deliberately so. ``Store.add_artifact`` assigns each row's part
ordinal inside its own INSERT, as ``MAX(part) + 1`` for that version and kind, so
**the order the rows are written is the numbering**. Three call sites deciding
that order independently would be three chances for a part to be filed under a
number its filename does not match -- a mismatch nothing raises on, because both
halves are individually well formed.

The build writes its files and this reads them back; the naming contract they
share lives in :mod:`cadless.exporters`, next to the writer.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from cadless.exporters import EXPORTERS, part_index


def exported_parts(src_dir: Path, kind: str) -> list[Path]:
    """Every file of ``kind`` this build wrote, in part order.

    Ordered on the number parsed out of the name, never on the name itself: as
    text ``model_p10`` sorts before ``model_p2``, and since this order decides the
    ordinal each file is filed under, a lexicographic sort would quietly file part
    ten as part one. A file whose stem carries no number is skipped rather than
    guessed at.

    A one-solid build wrote ``model.{kind}`` and is returned as the single part it
    is. The two namings never share a directory -- the worker clears the previous
    build's files before writing -- so finding the plain name is enough to know
    which shape this directory holds.
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


async def copy_and_register(store, version_id: int, src_dir: str | Path) -> int:
    """Copy every exported part in, register each, and return how many were written.

    The filename is carried across unchanged, so what is on disk under the version
    is what the build called it, and the part ordinal the row receives is the
    position in the order above.
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
