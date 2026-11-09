"""A catalog version of several parts given the motion its viewer plays.

The fill is driven directly for what it builds and what it files, with the build
scripted; one test builds a real two-part item through the worker; and the app
is driven for when the fill runs -- after startup without startup waiting for it,
not at all when switched off, and after an import.
"""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import catalog_motion
from backend.app import create_app
from backend.catalog_motion import fill_motions
from backend.catalog_state import ledger_for
from cadless.assembly_guide import MOTION_FILE
from cadless.config import settings
from cadless.pipeline import Rebuild
from cadless.scoped_store import system_view
from cadless.store import Store
from cadless.worker import ExecResult
from tests.cls_fixtures import packed

TWO_PARTS = (
    "from build123d import *\n"
    "base = Box(40, 20, 10)\n"
    "lid = Pos(0, 0, 7) * Box(40, 20, 4)\n"
    "base.label = 'base'\n"
    "lid.label = 'lid'\n"
    "result = Compound(children=[base, lid])\n"
)
TWO_PARTS_SIZE = (40.0, 20.0, 14.0)


@pytest.fixture(autouse=True)
def isolate(monkeypatch, tmp_path):
    # Catalog roots of its own: the bundled catalog has no version of several
    # parts, and loading it would only slow every start here.
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    (tmp_path / "data").mkdir()
    monkeypatch.setattr(settings, "catalog_root", tmp_path / "catalog")


@pytest.fixture
def store(tmp_path):
    s = Store(db_path=tmp_path / "data" / "db.sqlite", artifacts_dir=tmp_path / "artifacts")
    asyncio.run(s.init())
    return s


def _seed(
    store: Store,
    item_id: str = "kit",
    versions: tuple = ((TWO_PARTS, 2, False, TWO_PARTS_SIZE),),
    *,
    domain: str = "mechanical",
) -> tuple[int, list[int]]:
    """A loaded catalog item; each version ``(code, glb parts, has a motion, filed size)``."""

    async def _run() -> tuple[int, list[int]]:
        build = system_view(store)
        project = await build.create_project(item_id.title(), catalog_item_id=item_id)
        ids = []
        for code, parts, moving, size in versions:
            version = await build.add_version(project.id, "a step", code, ok=True, bbox=size)
            directory = Path(build.version_artifact_dir(version.id))
            for i in range(parts):
                baked = directory / f"model_p{i}.glb"
                baked.write_bytes(b"baked")
                await build.add_artifact(version.id, "glb", str(baked))
            if moving:
                motion = directory / MOTION_FILE
                motion.write_text('{"order": [1, 0]}')
                await build.add_artifact(version.id, "assembly", str(motion))
            ids.append(version.id)
        return project.id, ids

    seeded = asyncio.run(_run())
    ledger_for(store).record(item_id, len(versions), name=item_id, domain=domain)
    return seeded


def _scripted(order=(1, 0), *, ok=True, ordered=True, size=TWO_PARTS_SIZE, calls=None):
    """Stands in for ``rebuild``, writing a motion where an accepted build of ``order``
    would and none where a refused one would not -- a refused verdict still carries
    the order its measurement found, as a real one does."""

    def run(code, *, export_dir=None, describe=True, export_scale=1.0, assembly_clock=False):
        if calls is not None:
            calls.append(
                {
                    "code": code,
                    "describe": describe,
                    "export_scale": export_scale,
                    "assembly_clock": assembly_clock,
                }
            )
        if not ok:
            failed = ExecResult(ok=False, error="NameError: x")
            return Rebuild(failed, False, failed.error, None, None)
        if ordered and export_dir:
            (Path(export_dir) / MOTION_FILE).write_text(json.dumps({"order": list(order)}))
        verdict = {
            "ok": ordered,
            "order": list(order),
            "failures": [] if ordered else ["parts 0 and 1 share 3.0 mm^3"],
        }
        result = ExecResult(ok=True, volume=1.0, bbox=size, part_count=len(order))
        return Rebuild(result, True, None, verdict, None)

    return run


def _fill(store: Store, **kwargs) -> int:
    return asyncio.run(fill_motions(store, ledger_for(store), **kwargs))


def _motions(store: Store, version_id: int) -> list:
    async def _run() -> list:
        return [a for a in await store.list_artifacts(version_id) if a.kind == "assembly"]

    return asyncio.run(_run())


def test_a_version_of_several_parts_is_given_the_motion_its_build_wrote(store):
    _, [version] = _seed(store)
    calls: list = []

    assert _fill(store, run=_scripted(calls=calls)) == 1

    [motion] = _motions(store, version)
    assert json.loads(Path(motion.path).read_text())["order"] == [1, 0]
    # Built once, the motion alone, on the clock an order search is given.
    assert calls == [
        {"code": TWO_PARTS, "describe": False, "export_scale": 1.0, "assembly_clock": True}
    ]


def test_the_build_is_at_the_scale_the_item_s_units_call_for(store):
    # Authored in metres and filed in millimetres, as the loader files a house.
    _, [version] = _seed(store, domain="house")
    calls: list = []

    filed = _fill(store, run=_scripted(size=(0.04, 0.02, 0.014), calls=calls))

    assert (filed, calls[0]["export_scale"]) == (1, 1000.0)
    assert len(_motions(store, version)) == 1


def test_versions_of_one_part_or_already_moving_are_not_built(store):
    _seed(
        store,
        versions=((TWO_PARTS, 1, False, TWO_PARTS_SIZE), (TWO_PARTS, 2, True, TWO_PARTS_SIZE)),
    )
    calls: list = []

    assert _fill(store, run=_scripted(calls=calls)) == 0
    assert calls == []


def test_code_the_gate_refuses_is_not_built(store):
    _, [version] = _seed(store, versions=(("import os\nresult = 1\n", 2, False, TWO_PARTS_SIZE),))
    calls: list = []

    assert _fill(store, run=_scripted(calls=calls)) == 0
    assert (calls, _motions(store, version)) == ([], [])


@pytest.mark.parametrize(
    ("build", "filed_size", "said"),
    [
        (_scripted(ok=False), TWO_PARTS_SIZE, "its build failed: NameError: x"),
        (_scripted(ordered=False), TWO_PARTS_SIZE, "could not be ordered: parts 0 and 1 share"),
        (_scripted(order=(2, 1, 0)), TWO_PARTS_SIZE, "ordered 3 parts where the version draws 2"),
        (_scripted(size=(40.0, 20.0, 15.0)), TWO_PARTS_SIZE, "where the version filed"),
        (_scripted(), None, "no size to hold its build to"),
    ],
    ids=["failed", "not ordered", "another part count", "another size", "no size filed"],
)
def test_a_build_that_might_describe_another_model_files_nothing(
    store, caplog, build, filed_size, said
):
    # A motion is filed for good, and a version with none is drawn as it always
    # was; one ordered from something other than what was baked would play parts
    # the viewer is not drawing. Why is logged, the one place an operator whose
    # kit does not play can find out.
    _, [version] = _seed(store, versions=((TWO_PARTS, 2, False, filed_size),))

    with caplog.at_level("WARNING", logger="backend.catalog_motion"):
        assert _fill(store, run=build) == 0

    assert _motions(store, version) == []
    assert said in caplog.text


def test_one_version_that_cannot_be_built_costs_only_itself(store):
    other = TWO_PARTS + "# the next step\n"
    _, [first, second] = _seed(
        store,
        versions=((TWO_PARTS, 2, False, TWO_PARTS_SIZE), (other, 2, False, TWO_PARTS_SIZE)),
    )
    scripted = _scripted()

    def run(code, **kwargs):
        if code == TWO_PARTS:
            raise RuntimeError("the worker went away")
        return scripted(code, **kwargs)

    assert _fill(store, run=run) == 1
    assert (len(_motions(store, first)), len(_motions(store, second))) == (0, 1)


def test_an_item_whose_units_are_unknown_is_not_built(store):
    # Built at a guessed scale, its parts would be ordered against clearances a
    # thousand times off.
    _seed(store, domain="no-such-domain")
    calls: list = []

    assert _fill(store, run=_scripted(calls=calls)) == 0
    assert calls == []


def test_a_ledger_that_cannot_be_read_builds_nothing(store):
    _seed(store)
    ledger_for(store).path.write_text("{ not json")
    calls: list = []

    assert _fill(store, run=_scripted(calls=calls)) == 0
    assert calls == []


def test_an_ask_for_one_project_builds_that_project_alone(store):
    asked, [wanted] = _seed(store, "asked")
    _, [other] = _seed(store, "other")

    assert _fill(store, project_ids=[asked], run=_scripted()) == 1
    assert (len(_motions(store, wanted)), len(_motions(store, other))) == (1, 0)


def test_a_motion_filed_while_the_version_was_built_is_not_filed_twice(store, tmp_path):
    # Startup and an import can both have asked for the same item; the second to
    # finish finds the first one's row and leaves it.
    _, [version] = _seed(store, versions=((TWO_PARTS, 2, True, TWO_PARTS_SIZE),))
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / MOTION_FILE).write_text('{"order": [0, 1]}')

    filed = asyncio.run(catalog_motion._file(system_view(store), version, staging))

    assert filed is False
    assert len(_motions(store, version)) == 1


def test_a_version_gone_while_it_was_built_is_left_gone(store, tmp_path):
    # A reload or a delete of the item can land between the build and the filing;
    # writing the motion then would leave a file behind that nothing refers to.
    project, [version] = _seed(store)
    asyncio.run(store.delete_project(project))
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / MOTION_FILE).write_text('{"order": [1, 0]}')

    filed = asyncio.run(catalog_motion._file(system_view(store), version, staging))

    assert filed is False
    assert not (Path(store.artifacts_dir) / str(version) / MOTION_FILE).exists()


@pytest.mark.build123d
def test_a_real_two_part_item_built_through_the_worker_gets_a_motion_the_viewer_plays(store):
    _, [version] = _seed(store)

    assert _fill(store) == 1

    [motion] = _motions(store, version)
    played = json.loads(Path(motion.path).read_text())
    # What the viewer checks before it plays: the order names exactly the parts it
    # draws, and at least one of them has a way out.
    assert sorted(played["order"]) == [0, 1]
    assert any(played["releases"])


def _held_build(release: threading.Event):
    scripted = _scripted()

    def run(code, **kwargs):
        release.wait(timeout=30)
        return scripted(code, **kwargs)

    return run


def test_startup_answers_while_the_fill_is_still_building(store, monkeypatch):
    _, [version] = _seed(store)
    release = threading.Event()
    monkeypatch.setattr(catalog_motion, "rebuild", _held_build(release))
    app = create_app(store=store)

    try:
        with TestClient(app) as client:
            assert client.get("/health").status_code == 200
            assert _motions(store, version) == []
            release.set()
            client.portal.call(app.state.catalog_motions.idle)
    finally:
        release.set()

    assert len(_motions(store, version)) == 1


def test_switched_off_the_app_builds_nothing(store, monkeypatch):
    _, [version] = _seed(store)
    calls: list = []
    monkeypatch.setattr(catalog_motion, "rebuild", _scripted(calls=calls))
    monkeypatch.setattr(settings, "catalog_motions", "off")
    app = create_app(store=store)

    with TestClient(app) as client:
        client.portal.call(app.state.catalog_motions.idle)

    assert (calls, _motions(store, version)) == ([], [])


def _kit_package(tmp_path: Path) -> bytes:
    """A two-part kit, packed somewhere other than this machine's catalog."""
    item = tmp_path / "authored" / "box-kit"
    (item / "steps").mkdir(parents=True)
    (item / "artifacts" / "01").mkdir(parents=True)
    (item / "steps" / "01.py").write_text(TWO_PARTS)
    for i in range(2):
        (item / "artifacts" / "01" / f"model_p{i}.glb").write_bytes(b"baked")
    step = {
        "index": 1,
        "instruction": "A box and its lid.",
        "code": "steps/01.py",
        "geometry": {"volume": 11200.0, "bbox": list(TWO_PARTS_SIZE)},
        "artifacts": {"glb": "artifacts/01/model_p0.glb", "glb:1": "artifacts/01/model_p1.glb"},
    }
    manifest = {"id": "box-kit", "name": "Box Kit", "domain": "mechanical", "steps": [step]}
    (item / "manifest.json").write_text(json.dumps(manifest, indent=2))
    (item / "source.json").write_text(json.dumps({"license": "MIT"}, indent=2))
    return packed(item)


@pytest.mark.parametrize(("mode", "motions"), [("fill", 1), ("off", 0)])
def test_a_kit_imported_while_the_app_runs_is_given_its_motion_without_a_restart(
    store, monkeypatch, tmp_path, mode, motions
):
    # Switched off, the import itself must still succeed: it asks regardless.
    monkeypatch.setattr(catalog_motion, "rebuild", _scripted())
    monkeypatch.setattr(settings, "catalog_motions", mode)
    payload = _kit_package(tmp_path)
    app = create_app(store=store)

    with TestClient(app) as client:
        out = client.post(
            "/packages/import",
            content=payload,
            headers={"Content-Type": "application/octet-stream"},
        )
        assert out.status_code == 200, out.text
        client.portal.call(app.state.catalog_motions.idle)
        project = out.json()["project_id"]

    async def _versions() -> list:
        return await store.list_versions(project)

    [version] = asyncio.run(_versions())
    assert len(_motions(store, version.id)) == motions
