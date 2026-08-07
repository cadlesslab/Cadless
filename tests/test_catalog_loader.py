"""Loader tests (catalog Phase 1). asyncio.run + tmp_path; no build123d.

The loader never executes step code, so step files can hold any text and
artifacts can be tiny dummy bytes.
"""

import asyncio
import json
import logging
import shutil
import threading
from pathlib import Path

import pytest

from cadless.catalog import ledger as ledger_module
from cadless.catalog.ledger import Ledger
from cadless.catalog.loader import (
    clear_all,
    clear_house,
    list_state,
    load_all,
    load_house,
)
from cadless.store import Store


def run(coro):
    return asyncio.run(coro)


def _store(tmp_path: Path) -> Store:
    return Store(db_path=tmp_path / "db.sqlite", artifacts_dir=tmp_path / "arts")


def _write_house(catalog_dir: Path, house_id: str, n_steps: int) -> Path:
    house = catalog_dir / house_id
    (house / "steps").mkdir(parents=True, exist_ok=True)
    steps = []
    for i in range(1, n_steps + 1):
        (house / "steps" / f"{i:02d}.py").write_text(f"result = {i}\n")
        art = house / "artifacts" / f"{i:02d}"
        art.mkdir(parents=True, exist_ok=True)
        (art / "model.step").write_text("ISO-STEP")
        (art / "model.glb").write_bytes(b"glTF-bytes")
        steps.append(
            {
                "index": i,
                "instruction": f"do step {i}",
                "code": f"steps/{i:02d}.py",
                "artifacts": {
                    "step": f"artifacts/{i:02d}/model.step",
                    "glb": f"artifacts/{i:02d}/model.glb",
                },
            }
        )
    (house / "manifest.json").write_text(
        json.dumps({"id": house_id, "name": f"Name {house_id}", "steps": steps})
    )
    return house


def test_load_creates_project_versions_artifacts_chat(tmp_path):
    async def go():
        s = _store(tmp_path)
        await s.init()
        led = Ledger(tmp_path / "ledger.json")
        house = _write_house(tmp_path / "cat", "h1", 3)

        pid = await load_house(s, led, house)
        assert pid is not None

        versions = await s.list_versions(pid)
        assert [v.prompt for v in versions] == ["do step 1", "do step 2", "do step 3"]
        # parent chain
        assert versions[0].parent_version_id is None
        assert versions[1].parent_version_id == versions[0].id
        assert versions[2].parent_version_id == versions[1].id
        # artifacts on the last version
        arts = await s.list_artifacts(versions[-1].id)
        assert {a.kind for a in arts} == {"step", "glb"}
        # current version is the last step
        assert (await s.get_project(pid)).current_version_id == versions[-1].id
        # chat replay: 2 messages per step
        session = await s.get_or_create_session(pid)
        msgs = await s.list_messages(session.id)
        assert len(msgs) == 6
        # ledger recorded
        assert await s.project_id_for_catalog_item("h1") == pid

    run(go())


def test_load_is_idempotent(tmp_path):
    async def go():
        s = _store(tmp_path)
        await s.init()
        led = Ledger(tmp_path / "ledger.json")
        house = _write_house(tmp_path / "cat", "h1", 2)
        pid = await load_house(s, led, house)
        again = await load_house(s, led, house)
        assert again is None
        assert len(await s.list_projects()) == 1
        assert pid is not None

    run(go())


def test_clear_removes_only_catalog_projects(tmp_path):
    async def go():
        s = _store(tmp_path)
        await s.init()
        led = Ledger(tmp_path / "ledger.json")
        house = _write_house(tmp_path / "cat", "h1", 2)
        pid = await load_house(s, led, house)
        mine = await s.create_project("my own project")

        assert await clear_house(s, led, "h1") is True
        assert await s.get_project(pid) is None
        assert led.get("h1") is None
        # artifact dir for a removed version is gone
        assert not (tmp_path / "arts" / str(pid)).exists()
        # user project survives clear_all
        await clear_all(s, led)
        assert await s.get_project(mine.id) is not None

    run(go())


def test_clear_self_heals_stale_ledger(tmp_path):
    async def go():
        s = _store(tmp_path)
        await s.init()
        led = Ledger(tmp_path / "ledger.json")
        house = _write_house(tmp_path / "cat", "h1", 1)
        pid = await load_house(s, led, house)
        await s.delete_project(pid)  # project gone behind the ledger's back
        assert await clear_house(s, led, "h1") is True
        assert led.get("h1") is None

    run(go())


def test_reload_recreates(tmp_path):
    async def go():
        s = _store(tmp_path)
        await s.init()
        led = Ledger(tmp_path / "ledger.json")
        house = _write_house(tmp_path / "cat", "h1", 2)
        pid1 = await load_house(s, led, house)
        pid2 = await load_house(s, led, house, reload=True)
        assert pid2 is not None and pid2 != pid1
        assert list(led.entries()) == ["h1"]
        assert len(await s.list_projects()) == 1

    run(go())


def test_load_extracts_params_block_onto_versions(tmp_path):
    """A step whose code declares a ``params`` block becomes editable in the UI."""

    async def go():
        s = _store(tmp_path)
        await s.init()
        led = Ledger(tmp_path / "ledger.json")
        house = tmp_path / "cat" / "p1"
        (house / "steps").mkdir(parents=True)
        (house / "steps" / "01.py").write_text(
            "from build123d import *\n"
            'params = {"length": 40, "width": 20}\n'
            'result = Box(params["length"], params["width"], 8)\n'
        )
        (house / "manifest.json").write_text(
            json.dumps(
                {
                    "id": "p1",
                    "name": "Plate",
                    "steps": [{"index": 1, "instruction": "s1", "code": "steps/01.py"}],
                }
            )
        )
        pid = await load_house(s, led, house)
        versions = await s.list_versions(pid)
        assert versions[-1].parameters == {"length": 40, "width": 20}

    run(go())


def test_load_no_params_block_yields_empty_params(tmp_path):
    async def go():
        s = _store(tmp_path)
        await s.init()
        led = Ledger(tmp_path / "ledger.json")
        house = _write_house(tmp_path / "cat", "h1", 1)  # code is "result = 1"
        pid = await load_house(s, led, house)
        versions = await s.list_versions(pid)
        assert versions[-1].parameters == {}

    run(go())


def test_load_replays_stored_transcript(tmp_path):
    """A step with a transcript replays the real user/assistant turns verbatim."""

    async def go():
        s = _store(tmp_path)
        await s.init()
        led = Ledger(tmp_path / "ledger.json")
        house = tmp_path / "cat" / "t1"
        (house / "steps").mkdir(parents=True)
        (house / "steps" / "01.py").write_text("result = 1\n")
        (house / "manifest.json").write_text(
            json.dumps(
                {
                    "id": "t1",
                    "name": "T",
                    "steps": [
                        {
                            "index": 1,
                            "instruction": "do step 1",
                            "code": "steps/01.py",
                            "transcript": {
                                "user_prompt": "Make a 10mm cube.",
                                "assistant_message": "Here's your 10mm cube.",
                            },
                        }
                    ],
                }
            )
        )
        pid = await load_house(s, led, house)
        session = await s.get_or_create_session(pid)
        msgs = await s.list_messages(session.id)
        assert (msgs[0].role, msgs[0].content) == ("user", "Make a 10mm cube.")
        assert (msgs[1].role, msgs[1].content) == ("assistant", "Here's your 10mm cube.")

    run(go())


def test_load_falls_back_to_placeholder_without_transcript(tmp_path):
    """Legacy steps (no transcript) keep the instruction + placeholder reply."""

    async def go():
        s = _store(tmp_path)
        await s.init()
        led = Ledger(tmp_path / "ledger.json")
        house = _write_house(tmp_path / "cat", "h1", 1)
        pid = await load_house(s, led, house)
        session = await s.get_or_create_session(pid)
        msgs = await s.list_messages(session.id)
        assert (msgs[0].role, msgs[0].content) == ("user", "do step 1")
        assert msgs[1].role == "assistant" and "do step 1" in msgs[1].content

    run(go())


def test_list_state(tmp_path):
    async def go():
        s = _store(tmp_path)
        await s.init()
        led = Ledger(tmp_path / "ledger.json")
        cat = tmp_path / "cat"
        _write_house(cat, "h1", 1)
        _write_house(cat, "h2", 1)
        pid = await load_house(s, led, cat / "h1")
        rows = await list_state(s, cat)
        assert {r["id"]: r["loaded"] for r in rows} == {"h1": True, "h2": False}
        assert {r["id"]: r["project_id"] for r in rows} == {"h1": pid, "h2": None}

    run(go())


def test_list_state_creates_no_database(tmp_path):
    """`list` only reports, so it must not leave a store where there was none.

    Nothing is loaded until something loads it, which makes "no database" and "an
    empty one" the same answer — and the data directory a listing points at is not
    necessarily one it can write.
    """

    async def go():
        s = Store(db_path=tmp_path / "absent" / "db.sqlite", artifacts_dir=tmp_path / "arts")
        cat = tmp_path / "cat"
        _write_house(cat, "h1", 1)

        rows = await list_state(s, cat)

        assert [(r["id"], r["loaded"], r["project_id"]) for r in rows] == [("h1", False, None)]
        assert not (tmp_path / "absent").exists(), "listing created a database"

    run(go())


def test_load_propagates_discovery_metadata_to_ledger(tmp_path):
    """category/tags/description flow from manifest into the ledger (#21)."""

    async def go():
        s = _store(tmp_path)
        await s.init()
        led = Ledger(tmp_path / "ledger.json")
        house = tmp_path / "cat" / "m1"
        (house / "steps").mkdir(parents=True)
        (house / "steps" / "01.py").write_text("result = 1\n")
        (house / "manifest.json").write_text(
            json.dumps(
                {
                    "id": "m1",
                    "name": "Meta House",
                    "category": "bungalow",
                    "tags": ["garage"],
                    "description": "Cosy bungalow.",
                    "steps": [{"index": 1, "instruction": "s1", "code": "steps/01.py"}],
                }
            )
        )
        await load_house(s, led, house)
        entry = led.get("m1")
        assert entry["category"] == "bungalow"
        assert entry["tags"] == ["garage"]
        assert entry["description"] == "Cosy bungalow."
        assert entry["thumbnail"] is False

    run(go())


def test_load_copies_thumbnail_onto_final_version(tmp_path):
    """The baked thumbnail lands beside the final version's artifacts (#21)."""

    async def go():
        s = _store(tmp_path)
        await s.init()
        led = Ledger(tmp_path / "ledger.json")
        house = _write_house(tmp_path / "cat", "h1", 2)
        (house / "artifacts" / "thumbnail.png").write_bytes(b"\x89PNG fake")
        manifest = json.loads((house / "manifest.json").read_text())
        manifest["thumbnail"] = "artifacts/thumbnail.png"
        (house / "manifest.json").write_text(json.dumps(manifest))

        pid = await load_house(s, led, house)
        versions = await s.list_versions(pid)
        art = await s.get_artifact(versions[-1].id, "thumbnail")
        assert art is not None
        assert Path(art.path).read_bytes() == b"\x89PNG fake"
        # earlier versions carry no thumbnail
        assert await s.get_artifact(versions[0].id, "thumbnail") is None
        assert led.get("h1")["thumbnail"] is True

    run(go())


def test_load_missing_thumbnail_file_is_tolerated(tmp_path):
    """A manifest thumbnail whose file is absent loads without one (#21)."""

    async def go():
        s = _store(tmp_path)
        await s.init()
        led = Ledger(tmp_path / "ledger.json")
        house = _write_house(tmp_path / "cat", "h1", 1)
        manifest = json.loads((house / "manifest.json").read_text())
        manifest["thumbnail"] = "artifacts/thumbnail.png"  # never written
        (house / "manifest.json").write_text(json.dumps(manifest))

        pid = await load_house(s, led, house)
        versions = await s.list_versions(pid)
        assert await s.get_artifact(versions[-1].id, "thumbnail") is None
        assert led.get("h1")["thumbnail"] is False

    run(go())


def test_load_surfaces_source_provenance_in_ledger(tmp_path):
    """A source.json beside the manifest lands on the ledger entry (#23)."""

    async def go():
        s = _store(tmp_path)
        await s.init()
        led = Ledger(tmp_path / "ledger.json")
        house = _write_house(tmp_path / "cat", "h1", 1)
        provenance = {
            "dataset": "deepcad",
            "id": "00031420",
            "license": "Onshape public documents",
            "ingested_at": "2026-07-14T00:00:00+00:00",
        }
        (house / "source.json").write_text(json.dumps(provenance))

        await load_house(s, led, house)
        entry = led.get("h1")
        assert entry["source"] == provenance

    run(go())


def test_load_without_source_json_keeps_legacy_shape(tmp_path):
    async def go():
        s = _store(tmp_path)
        await s.init()
        led = Ledger(tmp_path / "ledger.json")
        house = _write_house(tmp_path / "cat", "h1", 1)
        await load_house(s, led, house)
        assert "source" not in led.get("h1")

    run(go())


def test_load_records_content_hash_and_skips_unchanged(tmp_path):
    """Incremental load (#23): unchanged items are skipped by content hash."""

    async def go():
        s = _store(tmp_path)
        await s.init()
        led = Ledger(tmp_path / "ledger.json")
        house = _write_house(tmp_path / "cat", "h1", 2)
        pid = await load_house(s, led, house)
        entry = led.get("h1")
        assert entry["content_hash"].startswith("sha256:")
        assert await load_house(s, led, house) is None
        assert await s.project_id_for_catalog_item("h1") == pid

    run(go())


def test_load_reloads_when_content_changed(tmp_path):
    """Incremental load (#23): a changed item is reloaded in place."""

    async def go():
        s = _store(tmp_path)
        await s.init()
        led = Ledger(tmp_path / "ledger.json")
        house = _write_house(tmp_path / "cat", "h1", 2)
        pid1 = await load_house(s, led, house)
        (house / "steps" / "01.py").write_text("result = 'changed'\n")

        pid2 = await load_house(s, led, house)
        assert pid2 is not None and pid2 != pid1
        assert len(await s.list_projects()) == 1  # old copy cleared
        assert await s.project_id_for_catalog_item("h1") == pid2

    run(go())


def test_load_legacy_ledger_entry_without_hash_still_skips(tmp_path):
    """Entries recorded before #23 have no content_hash: keep skipping them."""

    async def go():
        s = _store(tmp_path)
        await s.init()
        led = Ledger(tmp_path / "ledger.json")
        house = _write_house(tmp_path / "cat", "h1", 1)
        pid = await load_house(s, led, house)
        entry = led.entries()["h1"]
        del entry["content_hash"]
        led._write({**led.entries(), "h1": entry})

        assert await load_house(s, led, house) is None
        assert await s.project_id_for_catalog_item("h1") == pid

    run(go())


def test_load_reloads_when_provenance_changed(tmp_path):
    """source.json is part of the content hash: provenance edits refresh (#23)."""

    async def go():
        s = _store(tmp_path)
        await s.init()
        led = Ledger(tmp_path / "ledger.json")
        house = _write_house(tmp_path / "cat", "h1", 1)
        (house / "source.json").write_text(json.dumps({"license": "old"}))
        pid1 = await load_house(s, led, house)
        (house / "source.json").write_text(json.dumps({"license": "corrected"}))

        pid2 = await load_house(s, led, house)
        assert pid2 is not None and pid2 != pid1
        assert led.get("h1")["source"] == {"license": "corrected"}

    run(go())


def test_load_house_keeps_file_io_off_the_event_loop(tmp_path, monkeypatch):
    """A load must leave no file read or write on the loop's own thread.

    The api service is answering other requests while an import runs — the
    worker's among them — and every one of those waits behind whatever the loop
    is doing inline. A manifest, every step's source, the hash over all of them,
    each artifact copy and the ledger write are far more filesystem work than
    belongs there, so they go to a worker thread. ``Path.exists`` is deliberately
    not watched: a single stat costs nothing to leave behind, and watching it
    would catch unrelated library calls that happen to run during the load.
    """
    on_loop: list[str] = []
    loop_thread = threading.get_ident()

    def watch(owner, name):
        real = getattr(owner, name)

        def spy(*args, **kwargs):
            if threading.get_ident() == loop_thread:
                on_loop.append(name)
            return real(*args, **kwargs)

        monkeypatch.setattr(owner, name, spy)

    async def go():
        s = _store(tmp_path)
        await s.init()
        led = Ledger(tmp_path / "ledger.json")
        house = _write_house(tmp_path / "cat", "h1", 2)
        (house / "source.json").write_text(json.dumps({"license": "L"}))
        # Watch only from here: building the fixture above is the test's own I/O.
        for name in ("read_text", "read_bytes", "write_text"):
            watch(Path, name)
        watch(shutil, "copyfile")

        assert await load_house(s, led, house) is not None
        # A reload goes back through clear_house, whose ledger read and write
        # moved off the loop in the same change and would otherwise go unwatched.
        assert await load_house(s, led, house, reload=True) is not None

    run(go())
    assert on_loop == []


def test_a_missing_artifact_leaves_no_version_directory(tmp_path):
    """A source that is not there must not leave an empty artifact directory.

    The existence check is what guards the directory creation, and both travel
    to the worker thread together. Hoisting the one above the other would slip
    past every other test here, because they all write the artifacts they name.
    """

    async def go():
        s = _store(tmp_path)
        await s.init()
        led = Ledger(tmp_path / "ledger.json")
        house = _write_house(tmp_path / "cat", "h1", 1)
        manifest = json.loads((house / "manifest.json").read_text())
        manifest["steps"][0]["artifacts"] = {"step": "artifacts/never-baked/model.step"}
        (house / "manifest.json").write_text(json.dumps(manifest))

        pid = await load_house(s, led, house)
        versions = await s.list_versions(pid)
        assert await s.list_artifacts(versions[0].id) == []
        assert not (tmp_path / "arts" / str(versions[0].id)).exists()

    run(go())


def test_clear_interrupted_before_it_forgets_the_entry_is_rebuilt_next_load(tmp_path):
    """A clear that stops between its two halves must break the recoverable way.

    The project row goes first and the entry second, so stopping in between
    leaves the row gone and the entry stale. Whether an item is loaded is the
    row's answer, so the next start reads that as "not loaded" and builds the
    item again, entry and all. Forgetting the entry first would leave the
    opposite half, which the test below this one shows nothing repairs. This
    pins which half goes first, because both orders clear an item that is not
    interrupted and nothing else here would notice the swap.
    """

    async def go():
        s = _store(tmp_path)
        await s.init()
        led = Ledger(tmp_path / "ledger.json")
        catalog = tmp_path / "cat"
        house = _write_house(catalog, "h1", 2)
        first = await load_house(s, led, house)

        def boom(*_args, **_kwargs):  # the ledger half is called in a worker thread
            raise RuntimeError("stopped before the entry was forgotten")

        # Scoped to the clear: the load after it must meet a working ledger.
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(led, "remove", boom)
            with pytest.raises(RuntimeError):
                await clear_house(s, led, "h1")
            assert await s.project_id_for_catalog_item("h1") is None
            assert led.get("h1") is not None  # the stale half the next load repairs

        loaded = await load_all(s, led, catalog)
        assert loaded["h1"] is not None  # a load that raised would report None here
        again = await s.project_id_for_catalog_item("h1")
        assert again == loaded["h1"] and again != first
        assert len(await s.list_versions(again)) == 2
        assert led.get("h1")["step_count"] == 2

    run(go())


def test_a_row_whose_entry_was_forgotten_is_the_half_nothing_repairs(tmp_path):
    """The asymmetry the clear order turns on, asserted on its own.

    Forgetting the entry while the project row stays is the half that stops
    being repairable. The row answers "already loaded", so a load looks for a
    recorded hash to compare against and finds no entry at all — which it reads
    the way it reads an entry written before hashing existed, and skips. The
    item keeps the name-only listing a missing entry leaves it with, and no
    start changes that. Whoever revisits that skip is changing what the clear
    order is built on, so it is asserted here rather than left implied.
    """

    async def go():
        s = _store(tmp_path)
        await s.init()
        led = Ledger(tmp_path / "ledger.json")
        catalog = tmp_path / "cat"
        house = _write_house(catalog, "h1", 2)
        pid = await load_house(s, led, house)

        led.remove("h1")  # the half a reversed clear would leave behind

        assert await load_house(s, led, house) is None
        assert await load_all(s, led, catalog) == {"h1": None}
        assert await s.project_id_for_catalog_item("h1") == pid
        assert led.get("h1") is None

    run(go())


def test_clear_interrupted_before_it_deletes_the_project_leaves_the_item_whole(
    tmp_path, monkeypatch
):
    """The other stopping point must cost nothing at all.

    The row is deleted first, so a failure there is a clear that never started:
    the row and its entry are both still there and the item is the one it was.
    Forgetting the entry first would spend that half before the failure, and
    what it left would be the half-state no start repairs.
    """

    async def go():
        s = _store(tmp_path)
        await s.init()
        led = Ledger(tmp_path / "ledger.json")
        house = _write_house(tmp_path / "cat", "h1", 2)
        pid = await load_house(s, led, house)
        entry = led.get("h1")

        async def boom(*_args, **_kwargs):  # this half is awaited, not threaded
            raise RuntimeError("stopped before the project was deleted")

        monkeypatch.setattr(s, "delete_project", boom)
        with pytest.raises(RuntimeError):
            await clear_house(s, led, "h1")

        assert await s.project_id_for_catalog_item("h1") == pid
        assert led.get("h1") == entry

    run(go())


def test_a_ledger_another_process_is_holding_does_not_fail_the_load(tmp_path, monkeypatch, caplog):
    """The item still loads; only what the panel shows about it is missing.

    A writer in another process is a wait this one refuses rather than sits out.
    Failing the load instead would leave the item not loaded at all, and a retry
    would be skipped by the db the moment the row existed — so carrying on is
    the better of two imperfect answers, not a free one.

    What it costs is the entry, and it stays gone: `load_house` skips a row that
    says "already loaded" with nothing recorded beside it, so this does not come
    back on the next start. `catalog reload` is what rewrites it. The assertion
    on the empty ledger below is that cost, stated rather than implied.
    """
    monkeypatch.setattr(ledger_module, "LOCK_WAIT", 0.1)
    led = Ledger(tmp_path / "ledger.json")
    house = _write_house(tmp_path / "cat", "h1", 1)

    async def go():
        s = _store(tmp_path)
        await s.init()
        pid = await load_house(s, led, house)
        return pid, await s.project_id_for_catalog_item("h1")

    with open(led.lock_path, "a") as holder:
        ledger_module.fcntl.flock(holder, ledger_module.fcntl.LOCK_EX)
        with caplog.at_level(logging.WARNING):
            pid, marked = run(go())

    assert pid is not None, "the item must load even with the ledger held"
    assert marked == pid, "and be marked read-only, which is the db's answer"
    assert led.entries() == {}, "the entry is what was given up"
    assert "h1" in caplog.text, caplog.text


def test_a_reload_is_what_brings_a_given_up_entry_back(tmp_path, monkeypatch):
    """The remedy the other tests' comments name, held to actually working.

    An ordinary load will not do it: a row that says "already loaded" with no
    entry beside it is skipped, which is the whole reason a dropped entry is a
    lasting cost rather than a momentary one. `reload` is what goes back through
    `clear_house` and builds the item again, entry and all — so the advice this
    change gives an operator, and the one the startup path already gives after
    an unreadable ledger is moved aside, is checked rather than assumed.
    """
    monkeypatch.setattr(ledger_module, "LOCK_WAIT", 0.1)
    led = Ledger(tmp_path / "ledger.json")
    house = _write_house(tmp_path / "cat", "h1", 1)

    async def load(**kwargs):
        s = _store(tmp_path)
        await s.init()
        return await load_house(s, led, house, **kwargs)

    with open(led.lock_path, "a") as holder:
        ledger_module.fcntl.flock(holder, ledger_module.fcntl.LOCK_EX)
        assert run(load()) is not None
    assert led.entries() == {}, "the entry was dropped, which is what needs undoing"

    assert run(load()) is None, "an ordinary load skips it, which is why reload is the advice"

    assert run(load(reload=True)) is not None
    entry = led.get("h1")
    assert entry is not None, "`catalog reload` has to be able to rewrite the entry"
    assert entry["step_count"] == 1 and entry["name"] == "Name h1"


def test_a_clear_still_clears_when_another_process_holds_the_ledger(tmp_path, monkeypatch, caplog):
    """The project row is the half that decides, and it goes first regardless.

    A clear reached from an import holds the import gate, so parking here behind
    a process this one knows nothing about would stop every import on the
    machine. The entry left behind is the same leftover an unreadable ledger
    leaves, and the next load overwrites it — while the item is, by the only
    record that answers for read-only access, gone.
    """
    monkeypatch.setattr(ledger_module, "LOCK_WAIT", 0.1)
    led = Ledger(tmp_path / "ledger.json")
    house = _write_house(tmp_path / "cat", "h1", 1)

    async def load():
        s = _store(tmp_path)
        await s.init()
        return s, await load_house(s, led, house)

    store, pid = run(load())
    assert pid is not None and led.get("h1") is not None

    async def clear(s):
        return await clear_house(s, led, "h1"), await s.project_id_for_catalog_item("h1")

    with open(led.lock_path, "a") as holder:
        ledger_module.fcntl.flock(holder, ledger_module.fcntl.LOCK_EX)
        with caplog.at_level(logging.WARNING):
            cleared, still_marked = run(clear(store))

    assert cleared is True, "the project going is enough to count as cleared"
    assert still_marked is None, "and it did go"
    assert led.get("h1") is not None, "the entry is the half that was left"
    assert "held by another process; left a stale entry" in caplog.text, caplog.text
