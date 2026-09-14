"""Copying a build's exports into a version's artifact directory.

The ordinal an artifact row receives is decided by the order the rows are written,
so the order this module produces is not a presentation detail -- it *is* the
numbering, and getting it wrong files a part under another part's address without
raising anything.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.artifact_io import GUIDE_KIND, copy_and_register, exported_parts, guide_frames
from cadless.exporters import EXPORTERS
from cadless.store import Store


def _touch(directory: Path, *names: str) -> None:
    for name in names:
        (directory / name).write_bytes(b"x")


def test_parts_are_ordered_by_number_not_by_name(tmp_path):
    """Ten parts is where a lexicographic sort starts lying: as text ``model_p10``
    comes before ``model_p2``, and this order is the ordinal each file is filed
    under, so the mistake is silent at every layer below it."""
    _touch(tmp_path, *[f"model_p{i}.stl" for i in range(12)])

    names = [p.name for p in exported_parts(tmp_path, "stl")]

    assert names == [f"model_p{i}.stl" for i in range(12)]


def test_a_single_part_build_is_found_under_the_plain_name(tmp_path):
    _touch(tmp_path, "model.stl")

    assert [p.name for p in exported_parts(tmp_path, "stl")] == ["model.stl"]


def test_a_name_carrying_no_number_is_skipped_rather_than_guessed_at(tmp_path):
    """Anything else files it under an ordinal that belongs to a real part."""
    _touch(tmp_path, "model_p0.stl", "model_pX.stl", "model_p1.stl")

    names = [p.name for p in exported_parts(tmp_path, "stl")]

    assert names == ["model_p0.stl", "model_p1.stl"]


def test_the_plain_name_wins_where_both_namings_are_present(tmp_path):
    """A directory holding both is a build written over another's leftovers, and
    the export step is responsible for not leaving them. Pinned anyway, because
    the early return above is only the right answer while that holds -- if it
    stops holding, this is what the reader silently gets.
    """
    _touch(tmp_path, "model.stl", "model_p0.stl", "model_p1.stl")

    assert [p.name for p in exported_parts(tmp_path, "stl")] == ["model.stl"]


def test_only_the_kind_asked_for_comes_back(tmp_path):
    _touch(tmp_path, "model_p0.stl", "model_p0.step")

    assert [p.name for p in exported_parts(tmp_path, "step")] == ["model_p0.step"]


def test_guide_frames_are_ordered_by_number_not_by_name(tmp_path):
    """The same trap as the ten-parts case above, for the guide's own naming:
    as text ``guide_f10`` sorts before ``guide_f2``, and this order is the
    ordinal each frame is filed under."""
    _touch(tmp_path, *[f"guide_f{i}.png" for i in range(12)])

    names = [p.name for p in guide_frames(tmp_path)]

    assert names == [f"guide_f{i}.png" for i in range(12)]


def test_a_guide_frame_carrying_no_number_is_skipped_rather_than_guessed_at(tmp_path):
    _touch(tmp_path, "guide_f0.png", "guide_fX.png", "guide_f1.png")

    names = [p.name for p in guide_frames(tmp_path)]

    assert names == ["guide_f0.png", "guide_f1.png"]


@pytest.fixture
def store(tmp_path):
    """Brought up through the app's lifespan, which is what creates the schema."""
    s = Store(db_path=tmp_path / "db.sqlite", artifacts_dir=tmp_path / "artifacts")
    with TestClient(create_app(store=s)):
        yield s


def test_every_part_reaches_the_store_under_its_own_ordinal(store, tmp_path):
    """The whole point of the funnel: N files become N rows, numbered in the order
    the filenames say, so ``model_p3.stl`` is the artifact addressed as part 3."""
    src = tmp_path / "staging"
    src.mkdir()
    _touch(src, *[f"model_p{i}.stl" for i in range(4)])

    async def _run() -> list:
        project = await store.create_project("P")
        version = await store.add_version(project.id, "a shelf", "result=1", ok=True)
        await copy_and_register(store, version.id, src)
        return await store.list_artifacts(version.id)

    rows = asyncio.run(_run())

    stl = sorted((a for a in rows if a.kind == "stl"), key=lambda a: a.part)
    assert [a.part for a in stl] == [0, 1, 2, 3]
    assert [Path(a.path).name for a in stl] == [f"model_p{i}.stl" for i in range(4)]


def test_guide_frames_reach_the_store_though_guide_is_not_an_exported_kind(store, tmp_path):
    """The reason the funnel carries a second loop at all: ``guide`` is
    deliberately absent from ``EXPORTERS``, so the ``for kind in EXPORTERS`` loop
    above would never visit these files on its own -- they would be written to
    disk and then silently never registered, never found by a reader."""
    assert GUIDE_KIND not in EXPORTERS
    src = tmp_path / "staging"
    src.mkdir()
    _touch(src, "model.stl")
    _touch(src, *[f"guide_f{i}.png" for i in range(3)])

    async def _run() -> tuple[int, list]:
        project = await store.create_project("P")
        version = await store.add_version(project.id, "a shelf", "result=1", ok=True)
        written = await copy_and_register(store, version.id, src)
        rows = await store.list_artifacts(version.id)
        return written, rows

    written, rows = asyncio.run(_run())

    guide = sorted((a for a in rows if a.kind == "guide"), key=lambda a: a.part)
    assert [a.part for a in guide] == [0, 1, 2]
    assert [Path(a.path).name for a in guide] == [f"guide_f{i}.png" for i in range(3)]
    assert written == 4  # the one stl part plus the three guide frames
