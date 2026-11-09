"""A shipped catalog version of several parts given the motion its viewer plays.

The fill is driven directly for what it builds and what it files, with the build
scripted, and with a real two-part item built through the worker; the app is
driven for when the fill runs -- after startup without startup waiting for it,
not at all when switched off, and never for a received package.
"""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend import catalog_motion
from backend.app import create_app
from backend.catalog_motion import CatalogMotions, fill_motions
from backend.catalog_state import ledger_for
from cadless.assembly_check import AssemblyMeasurements
from cadless.assembly_guide import MOTION_FILE
from cadless.catalog.importer import RootScan, imported_catalog_root
from cadless.config import Settings, settings
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
# Each part's lowest and highest corner, in millimetres, as TWO_PARTS builds them.
BOXES = (((-20.0, -10.0, -5.0), (20.0, 10.0, 5.0)), ((-20.0, -10.0, 5.0), (20.0, 10.0, 9.0)))


@pytest.fixture(autouse=True)
def isolate(monkeypatch, tmp_path):
    # Catalog roots of this test's own, so what each start loads is what it seeds.
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    (tmp_path / "data").mkdir()
    monkeypatch.setattr(settings, "catalog_root", tmp_path / "catalog")


@pytest.fixture
def store(tmp_path):
    s = Store(db_path=tmp_path / "data" / "db.sqlite", artifacts_dir=tmp_path / "artifacts")
    asyncio.run(s.init())
    return s


def _stl(path: Path, low, high) -> None:
    """A mesh spanning exactly the box from ``low`` to ``high``."""
    (lx, ly, lz), (hx, hy, hz) = low, high
    facets = (
        ((lx, ly, lz), (hx, ly, lz), (hx, hy, hz)),
        ((lx, ly, lz), (lx, hy, hz), (hx, hy, hz)),
    )
    lines = ["solid part"]
    for facet in facets:
        lines += ["facet normal 0 0 0", "outer loop"]
        lines += [f"vertex {x} {y} {z}" for x, y, z in facet]
        lines += ["endloop", "endfacet"]
    path.write_text("\n".join([*lines, "endsolid part"]) + "\n")


def _seed(
    store: Store,
    item_id: str = "kit",
    versions: tuple = ((TWO_PARTS, BOXES, False),),
    *,
    domain: str = "mechanical",
    meshes: bool = True,
) -> tuple[int, list[int]]:
    """A loaded catalog item the catalog root holds; each version ``(code, part boxes,
    has a motion)``, its parts baked as glb files and, with ``meshes``, stl files."""
    shipped = settings.catalog_root / "mech-catalog" / f"item-{abs(hash(item_id))}"
    shipped.mkdir(parents=True)
    (shipped / "manifest.json").write_text(json.dumps({"id": item_id}))

    async def _run() -> tuple[int, list[int]]:
        build = system_view(store)
        project = await build.create_project("Kit", catalog_item_id=item_id)
        ids = []
        for code, boxes, moving in versions:
            version = await build.add_version(project.id, "a step", code, ok=True)
            directory = Path(build.version_artifact_dir(version.id))
            for i, _ in enumerate(boxes):
                (directory / f"model_p{i}.glb").write_bytes(b"baked")
                await build.add_artifact(version.id, "glb", str(directory / f"model_p{i}.glb"))
            for i, (low, high) in enumerate(boxes if meshes else ()):
                _stl(directory / f"model_p{i}.stl", low, high)
                await build.add_artifact(version.id, "stl", str(directory / f"model_p{i}.stl"))
            if moving:
                (directory / MOTION_FILE).write_text('{"order": [1, 0]}')
                await build.add_artifact(version.id, "assembly", str(directory / MOTION_FILE))
            ids.append(version.id)
        return project.id, ids

    seeded = asyncio.run(_run())
    ledger_for(store).record(item_id, len(versions), name=item_id, domain=domain)
    return seeded


def _scripted(
    order=(1, 0), *, ok=True, ordered=True, trapped=False, note=None, boxes=BOXES, calls=None
):
    """Stands in for ``rebuild``: exports the parts' meshes, and writes a motion where
    an accepted build of ``order`` would and none where a refused one would not -- a
    refused verdict still carries the order its measurement found, as a real one does.
    ``trapped`` and ``note`` are a search that found no order, saying why in the
    measurement alone, as the parts checks leave it."""

    def run(code, *, export_dir=None, describe=True, export_scale=1.0):
        if calls is not None:
            calls.append({"code": code, "describe": describe, "export_scale": export_scale})
        if not ok:
            failed = ExecResult(ok=False, error="NameError: x")
            return Rebuild(failed, False, failed.error, None, None)
        for i, (low, high) in enumerate(boxes):
            _stl(Path(export_dir) / f"model_p{i}.stl", low, high)
        unordered = trapped or note is not None
        measured = AssemblyMeasurements(
            order=None if unordered else list(order),
            trapped=[0, 1] if trapped else [],
            unchecked=[note] if note is not None else [],
        )
        if unordered:
            verdict = {"ok": True, "order": None, "failures": [], "unchecked": []}
        else:
            verdict = {
                "ok": ordered,
                "order": list(order),
                "failures": [] if ordered else ["parts 0 and 1 share 3.0 mm^3"],
            }
        if verdict["ok"] and verdict["order"]:
            (Path(export_dir) / MOTION_FILE).write_text(json.dumps({"order": list(order)}))
        result = ExecResult(ok=True, volume=1.0, bbox=(40.0, 20.0, 14.0), assembly=measured)
        return Rebuild(result, True, None, verdict, None)

    return run


def _fill(store: Store, **kwargs) -> int:
    return asyncio.run(fill_motions(store, **kwargs))


def _motions(store: Store, version_id: int) -> list:
    async def _run() -> list:
        return [a for a in await store.list_artifacts(version_id) if a.kind == "assembly"]

    return asyncio.run(_run())


def _moved(boxes, dz: float):
    """``boxes`` with the second part raised by ``dz`` millimetres."""
    (low, high) = boxes[1]
    return (boxes[0], ((low[0], low[1], low[2] + dz), (high[0], high[1], high[2] + dz)))


def test_a_version_of_several_parts_is_given_the_motion_its_build_wrote(store):
    _, [version] = _seed(store)
    calls: list = []

    assert _fill(store, run=_scripted(calls=calls)) == 1

    [motion] = _motions(store, version)
    assert json.loads(Path(motion.path).read_text())["order"] == [1, 0]
    assert calls == [{"code": TWO_PARTS, "describe": False, "export_scale": 1.0}]


def test_the_build_is_at_the_scale_the_item_s_units_call_for(store):
    # Authored in metres; exported, as it was baked, in millimetres.
    _, [version] = _seed(store, domain="house")
    calls: list = []

    filed = _fill(store, run=_scripted(calls=calls))

    assert (filed, calls[0]["export_scale"]) == (1, 1000.0)
    assert len(_motions(store, version)) == 1


def test_a_version_of_one_part_is_not_built(store):
    _seed(store, versions=((TWO_PARTS, BOXES[:1], False),))
    calls: list = []

    assert _fill(store, run=_scripted(calls=calls)) == 0
    assert calls == []


def test_a_version_already_moving_is_not_built(store):
    _seed(store, versions=((TWO_PARTS, BOXES, True),))
    calls: list = []

    assert _fill(store, run=_scripted(calls=calls)) == 0
    assert calls == []


def test_code_the_gate_refuses_is_not_built(store):
    _, [version] = _seed(store, versions=(("import os\nresult = 1\n", BOXES, False),))
    calls: list = []

    assert _fill(store, run=_scripted(calls=calls)) == 0
    assert (calls, _motions(store, version)) == ([], [])


def test_a_version_with_no_part_meshes_is_not_built(store, caplog):
    # Nothing to hold the build's parts to, so nothing could show they are the
    # parts the viewer draws.
    _seed(store, meshes=False)
    calls: list = []

    with caplog.at_level("WARNING", logger="backend.catalog_motion"):
        assert _fill(store, run=_scripted(calls=calls)) == 0

    assert calls == []
    assert "no part meshes to hold a build to" in caplog.text


@pytest.mark.parametrize(
    ("build", "said"),
    [
        (_scripted(ok=False), "its build failed: 'NameError: x'"),
        (_scripted(ordered=False), "could not be ordered: 'parts 0 and 1 share"),
        (_scripted(trapped=True), "could not be ordered: 'parts [0, 1] could not be freed"),
        (_scripted(note="the order search ran out of time"), "'the order search ran out of time"),
        (_scripted(order=(2, 1, 0)), "ordered 3 parts where the version draws 2"),
        (_scripted(boxes=_moved(BOXES, 2.0)), "its part 1 spans"),
        (_scripted(boxes=BOXES[::-1]), "its part 0 spans"),
    ],
    ids=[
        "failed",
        "not ordered",
        "trapped",
        "out of time",
        "another part count",
        "a part moved",
        "parts swapped",
    ],
)
def test_a_build_that_might_describe_another_model_files_nothing(store, caplog, build, said):
    # A motion is filed for good, and a version with none is drawn as it always
    # was; one built from something other than what was baked would play parts
    # the viewer is not drawing. Why is logged, the one place an operator whose
    # kit does not play can find out.
    _, [version] = _seed(store)

    with caplog.at_level("WARNING", logger="backend.catalog_motion"):
        assert _fill(store, run=build) == 0

    assert _motions(store, version) == []
    assert said in caplog.text


def test_a_part_a_tessellation_away_from_the_baked_one_is_still_that_part(store):
    _, [version] = _seed(store)

    assert _fill(store, run=_scripted(boxes=_moved(BOXES, 0.1))) == 1
    assert len(_motions(store, version)) == 1


def test_an_item_that_cannot_be_read_costs_only_itself(store, monkeypatch):
    _, [first] = _seed(store, "first")
    _, [second] = _seed(store, "second")
    real = catalog_motion.authoring_units

    async def units(build, project):
        if project.catalog_item_id == "first":
            raise OSError("the ledger went away")
        return await real(build, project)

    monkeypatch.setattr(catalog_motion, "authoring_units", units)

    assert _fill(store, run=_scripted()) == 1
    assert (len(_motions(store, first)), len(_motions(store, second))) == (0, 1)


def test_one_version_that_cannot_be_built_costs_only_itself(store):
    other = TWO_PARTS + "# the next step\n"
    _, [first, second] = _seed(store, versions=((TWO_PARTS, BOXES, False), (other, BOXES, False)))
    scripted = _scripted()

    def run(code, **kwargs):
        if code == TWO_PARTS:
            raise RuntimeError("the worker went away")
        return scripted(code, **kwargs)

    assert _fill(store, run=run) == 1
    assert (len(_motions(store, first)), len(_motions(store, second))) == (0, 1)


def test_an_item_whose_units_are_unknown_is_not_built(store, caplog):
    # Built at a guessed scale, its parts would be ordered against clearances a
    # thousand times off. The id is quoted in the line saying so: it is the
    # item's to choose, and a line break in it would forge a line of its own.
    _seed(store, "kit\nINFO catalog motion: filed", domain="no-such-domain")
    calls: list = []

    with caplog.at_level("WARNING", logger="backend.catalog_motion"):
        assert _fill(store, run=_scripted(calls=calls)) == 0

    assert calls == []
    [said] = [r.getMessage() for r in caplog.records if "names no units" in r.getMessage()]
    assert "\n" not in said


def test_an_item_the_catalog_root_does_not_hold_is_not_built(store):
    # A row whose files are gone is not something the deployment ships.
    _seed(store)
    for manifest in settings.catalog_root.rglob("manifest.json"):
        manifest.unlink()
    calls: list = []

    assert _fill(store, run=_scripted(calls=calls)) == 0
    assert calls == []


def test_an_id_both_roots_answer_to_is_taken_for_received(store):
    # A package can share an id with a shipped item; which copy the row holds is
    # then not something this can tell, so the received one is assumed.
    _seed(store)
    received = imported_catalog_root() / "mech-catalog" / "kit"
    received.mkdir(parents=True)
    (received / "manifest.json").write_text(json.dumps({"id": "kit"}))
    calls: list = []

    assert _fill(store, run=_scripted(calls=calls)) == 0
    assert calls == []


def test_a_received_catalog_that_cannot_be_read_in_full_builds_nothing(store, monkeypatch):
    # Received items are told from shipped ones by walking the received root; a
    # walk that stepped over something cannot say an item is not received.
    _seed(store)
    monkeypatch.setattr(
        catalog_motion,
        "scan_root",
        lambda root: RootScan(ids=frozenset(), complete=False, present=True),
    )
    calls: list = []

    assert _fill(store, run=_scripted(calls=calls)) == 0
    assert calls == []


def test_a_ledger_that_cannot_be_read_builds_nothing(store):
    _seed(store)
    ledger_for(store).path.write_text("{ not json")
    calls: list = []

    assert _fill(store, run=_scripted(calls=calls)) == 0
    assert calls == []


def test_a_motion_filed_while_the_version_was_built_is_not_filed_twice(store, tmp_path):
    # Two app processes on one database each fill at their start; the second to
    # finish finds the first one's row and leaves it.
    _, [version] = _seed(store, versions=((TWO_PARTS, BOXES, True),))
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


def test_the_motion_waits_for_the_gate_it_is_filed_under(store):
    # The gate is what a reload or a delete of the item holds while it writes.
    _, [version] = _seed(store)
    calls: list = []

    async def _run() -> tuple[bool, list, int]:
        gate = asyncio.Lock()
        await gate.acquire()
        filling = asyncio.ensure_future(fill_motions(store, gate=gate, run=_scripted(calls=calls)))
        while not calls:
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.3)
        held = (filling.done(), await store.list_artifacts(version))
        gate.release()
        return held[0], [a for a in held[1] if a.kind == "assembly"], await filling

    done_while_held, motions_while_held, filed = asyncio.run(_run())

    assert (done_while_held, motions_while_held, filed) == (False, [], 1)


@pytest.mark.build123d
def test_a_real_two_part_item_built_through_the_worker_gets_a_motion_ordering_its_parts(store):
    _, [version] = _seed(store)

    assert _fill(store) == 1

    [motion] = _motions(store, version)
    played = json.loads(Path(motion.path).read_text())
    assert sorted(played["order"]) == [0, 1]
    assert any(played["releases"])


def test_a_route_s_scoped_view_is_refused(store):
    # Widened here, a view a route handed in would reach every principal's rows.
    with pytest.raises(TypeError, match="never a route's view"):
        asyncio.run(fill_motions(system_view(store)))


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


def test_the_app_files_under_its_import_gate(store, monkeypatch):
    # Held by a reload or a delete of the item while it writes; a fill that took
    # some other lock would write beside them.
    _, [version] = _seed(store)
    built = threading.Event()
    scripted = _scripted()

    def run(code, **kwargs):
        done = scripted(code, **kwargs)
        built.set()
        return done

    monkeypatch.setattr(catalog_motion, "rebuild", run)
    app = create_app(store=store)

    with TestClient(app) as client:
        client.portal.call(app.state.import_gate.acquire)
        try:
            assert built.wait(timeout=30)
            client.portal.call(asyncio.sleep, 0.3)
            assert _motions(store, version) == []
        finally:
            client.portal.call(app.state.import_gate.release)
        client.portal.call(app.state.catalog_motions.idle)

    assert len(_motions(store, version)) == 1


def test_a_pass_that_fails_leaves_the_app_serving(store, monkeypatch):
    async def broken(*args, **kwargs):
        raise RuntimeError("the catalog went away")

    monkeypatch.setattr(catalog_motion, "fill_motions", broken)
    app = create_app(store=store)

    with TestClient(app) as client:
        client.portal.call(app.state.catalog_motions.idle)
        assert client.get("/health").status_code == 200


def test_a_switch_value_it_does_not_know_stops_the_process_starting():
    # Read as either mode, an operator would believe something untrue about a
    # pass that runs catalog code through the worker.
    with pytest.raises(ValidationError, match="CADLESS_CATALOG_MOTIONS"):
        Settings(catalog_motions="sometimes")


@pytest.mark.parametrize(("configured", "read"), [("", "fill"), ("  OFF ", "off")])
def test_the_switch_reads_an_empty_value_as_unset_and_ignores_case(configured, read):
    assert Settings(catalog_motions=configured).catalog_motions == read


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
    for i, (low, high) in enumerate(BOXES):
        (item / "artifacts" / "01" / f"model_p{i}.glb").write_bytes(b"baked")
        _stl(item / "artifacts" / "01" / f"model_p{i}.stl", low, high)
    step = {
        "index": 1,
        "instruction": "A box and its lid.",
        "code": "steps/01.py",
        "geometry": {"volume": 11200.0, "bbox": [40.0, 20.0, 14.0]},
        "artifacts": {
            "glb": "artifacts/01/model_p0.glb",
            "glb:1": "artifacts/01/model_p1.glb",
            "stl": "artifacts/01/model_p0.stl",
            "stl:1": "artifacts/01/model_p1.stl",
        },
    }
    manifest = {"id": "box-kit", "name": "Box Kit", "domain": "mechanical", "steps": [step]}
    (item / "manifest.json").write_text(json.dumps(manifest, indent=2))
    (item / "source.json").write_text(json.dumps({"license": "MIT"}, indent=2))
    return packed(item)


def test_a_received_kit_is_not_built_at_its_import_or_at_the_next_start(
    store, monkeypatch, tmp_path
):
    # A package arrives through a route open to any caller the deployment admits; building its
    # steps by itself would run code nobody asked to run, as often as anyone
    # cared to send one.
    calls: list = []
    monkeypatch.setattr(catalog_motion, "rebuild", _scripted(calls=calls))
    payload = _kit_package(tmp_path)

    first = create_app(store=store)
    with TestClient(first) as client:
        out = client.post(
            "/packages/import",
            content=payload,
            headers={"Content-Type": "application/octet-stream"},
        )
        assert out.status_code == 200, out.text
        client.portal.call(first.state.catalog_motions.idle)
    again = create_app(store=store)
    with TestClient(again) as client:
        client.portal.call(again.state.catalog_motions.idle)

    async def _versions() -> list:
        return await store.list_versions(out.json()["project_id"])

    [version] = asyncio.run(_versions())
    assert (calls, _motions(store, version.id)) == ([], [])


def test_closed_mid_build_it_lets_the_build_finish_and_leaves_no_staging_behind(store):
    # The build carries on in its thread whatever happens to the task; a directory
    # removed under it would come back as it wrote its files, with nothing to
    # remove it again.
    _, [version] = _seed(store)
    started, release = threading.Event(), threading.Event()
    seen: dict = {}
    scripted = _scripted()

    def run(code, *, export_dir=None, **kwargs):
        seen["staging"] = Path(export_dir)
        started.set()
        release.wait(timeout=30)
        Path(export_dir).mkdir(parents=True, exist_ok=True)  # as the exporters do
        return scripted(code, export_dir=export_dir, **kwargs)

    async def _run() -> bool:
        motions = CatalogMotions(store, run=run)
        motions.start()
        await asyncio.to_thread(started.wait, 30)
        closing = asyncio.ensure_future(motions.close())
        await asyncio.sleep(0.3)
        waited = not closing.done()
        release.set()
        await closing
        return waited

    try:
        assert asyncio.run(_run()) is True
    finally:
        release.set()
    assert not seen["staging"].exists()
    assert _motions(store, version) == []
