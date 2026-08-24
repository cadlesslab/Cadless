"""Artifact GC sweeper tests."""

import asyncio
import logging
import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.app import create_app
from cadless.config import SWEEP_MODES, Settings, settings
from cadless.store import ReferencesUnrecognisable, Store


def _store(tmp_path):
    return Store(db_path=tmp_path / "db.sqlite", artifacts_dir=tmp_path / "artifacts")


def test_sweep_deletes_orphans_keeps_referenced(tmp_path):
    async def go():
        s = _store(tmp_path)
        await s.init()
        p = await s.create_project("P")
        v = await s.add_version(p.id, "x", "result=1", ok=True)
        d = Path(s.version_artifact_dir(v.id))
        referenced = d / "model.step"
        referenced.write_text("keep")
        await s.add_artifact(v.id, "step", str(referenced))
        orphan = d / "stale.glb"
        orphan.write_bytes(b"drop")

        # No grace, so a file written a moment ago counts — which is what this
        # case is about. The default is deliberately not zero; see
        # `test_the_default_grace_protects_something_written_now`.
        listed = await s.sweep_orphans(dry_run=True, grace_days=0)
        assert str(orphan) in listed
        assert orphan.exists() and referenced.exists()

        # real sweep removes the orphan, keeps the referenced file
        deleted = await s.sweep_orphans(grace_days=0)
        assert str(orphan) in deleted
        assert not orphan.exists()
        assert referenced.exists()

    asyncio.run(go())


def test_a_path_written_in_another_form_is_still_recognised(tmp_path):
    """The row and the walk must agree about which file they mean.

    `add_artifact` stores whatever string it was handed, and the sweep walks
    with `rglob`. Compared as text, the same file spelled two ways is two
    files — and the second one has no row, so it is an orphan. This is not
    hypothetical: `data_dir` defaults to the relative `runtime-db` and Docker
    sets the absolute `/data`, so a database written under one and swept under
    the other reports *every* artifact as unreferenced.
    """

    async def go():
        s = _store(tmp_path)
        await s.init()
        p = await s.create_project("P")
        v = await s.add_version(p.id, "x", "result=1", ok=True)
        d = Path(s.version_artifact_dir(v.id))
        kept = d / "model.step"
        kept.write_text("keep")
        # A traversal, which `Path()` alone does NOT collapse — only `resolve()`
        # does. A `.` segment would not test anything: `str(Path("a/./b"))` is
        # already "a/b", so the mutation that removes `resolve()` survives it.
        detoured = os.path.join(str(d.parent), d.name, "..", d.name, "model.step")
        assert str(Path(detoured)) != str(kept), "the spelling must actually differ"
        await s.add_artifact(v.id, "step", detoured)

        assert await s.sweep_orphans(dry_run=True, grace_days=0) == []
        assert kept.exists()

    asyncio.run(go())


def test_it_refuses_a_sweep_in_which_no_row_matched_anything(tmp_path):
    """Read literally that says the whole tree is litter. It never means that.

    It means the rows and the walk disagree about how to spell a path — a
    relative row is resolved against the *process* working directory, so a
    database written by a checkout in the repository root and later opened by a
    container whose WORKDIR is elsewhere produces keys matching nothing.
    Normalising cannot fix that: the row does not record what it was relative
    to. Refusing is what stops "all of it" being the answer.
    """

    async def go():
        s = _store(tmp_path)
        await s.init()
        p = await s.create_project("P")
        v = await s.add_version(p.id, "x", "result=1", ok=True)
        d = Path(s.version_artifact_dir(v.id))
        real = d / "model.step"
        real.write_text("somebody's model")
        # A row that resolves nowhere near the file it is meant to name — the
        # shape a moved working directory produces. It has to exist, because
        # `add_artifact` stats it for its size.
        elsewhere = tmp_path / "elsewhere" / "artifacts" / str(v.id)
        elsewhere.mkdir(parents=True)
        decoy = elsewhere / "model.step"
        decoy.write_text("what the row thinks it points at")
        await s.add_artifact(v.id, "step", str(decoy))

        with pytest.raises(ReferencesUnrecognisable):
            await s.sweep_orphans(dry_run=True, grace_days=0)
        assert real.exists()

    asyncio.run(go())


def test_an_empty_database_is_not_a_broken_comparison(tmp_path):
    """No rows at all is a real state, not a failed match. Sweeping is correct."""

    async def go():
        s = _store(tmp_path)
        await s.init()
        s.artifacts_dir.mkdir(parents=True, exist_ok=True)
        litter = s.artifacts_dir / "left.glb"
        litter.write_bytes(b"x")

        assert str(litter) in await s.sweep_orphans(dry_run=True, grace_days=0)

    asyncio.run(go())


def test_a_file_a_live_version_keeps_without_a_row_is_not_an_orphan(tmp_path):
    """Some files are referenced by the code rather than by the table.

    A sliced print job sits in the version's directory and is read back by the
    send and download routes. It carries no row on purpose, and a rule that
    only knows about rows calls it garbage — while somebody is waiting to press
    Send.
    """

    async def go():
        s = _store(tmp_path)
        await s.init()
        p = await s.create_project("P")
        v = await s.add_version(p.id, "x", "result=1", ok=True)
        d = Path(s.version_artifact_dir(v.id))
        stl = d / "model.stl"
        stl.write_text("mesh")
        await s.add_artifact(v.id, "stl", str(stl))
        job = d / "print.gcode"
        job.write_text("G28")

        listed = await s.sweep_orphans(dry_run=True, grace_days=0, keep_names=("print.gcode",))
        assert str(job) not in listed

    asyncio.run(go())


def test_the_keep_rule_holds_when_the_tree_is_reached_by_another_name(tmp_path):
    """The two sides of the "is it beside something referenced" test must agree.

    They are built differently — one from the rows, one from the walk — so they
    only agree if both are normalised. Reached through a symlink the walk yields
    the link's spelling while the rows resolve to the real one, and a comparison
    that skips the normalising stops protecting sliced jobs without any test
    noticing, because a temp directory is already in its resolved form.
    """

    async def go():
        real = tmp_path / "real-artifacts"
        real.mkdir()
        link = tmp_path / "artifacts-link"
        link.symlink_to(real, target_is_directory=True)
        s = Store(db_path=tmp_path / "db.sqlite", artifacts_dir=link)
        await s.init()
        p = await s.create_project("P")
        v = await s.add_version(p.id, "x", "result=1", ok=True)
        d = Path(s.version_artifact_dir(v.id))
        stl = d / "model.stl"
        stl.write_text("mesh")
        await s.add_artifact(v.id, "stl", str(stl))
        job = d / "print.gcode"
        job.write_text("G28")
        old = time.time() - 30 * 86400
        os.utime(job, (old, old))

        listed = await s.sweep_orphans(dry_run=True, keep_names=("print.gcode",))
        assert listed == [], listed
        assert job.exists()

    asyncio.run(go())


def test_the_same_file_is_an_orphan_once_its_version_is_gone(tmp_path):
    """The protection is "beside something referenced", not "named this".

    Otherwise a deleted version's leftovers would be kept for ever on the
    strength of their filename.
    """

    async def go():
        s = _store(tmp_path)
        await s.init()
        p = await s.create_project("P")
        v = await s.add_version(p.id, "x", "result=1", ok=True)
        d = Path(s.version_artifact_dir(v.id))
        job = d / "print.gcode"
        job.write_text("G28")  # no registered artifact beside it

        listed = await s.sweep_orphans(dry_run=True, grace_days=0, keep_names=("print.gcode",))
        assert str(job) in listed

    asyncio.run(go())


def test_the_default_grace_protects_something_written_now(tmp_path):
    """A caller that names no grace must not sweep work in progress.

    Generation, chat and reparametrize all stage their exports *inside* the
    artifact tree, with no row, for the whole run. Swept, the run finishes with
    a version that has no artifacts and reports no error.
    """

    async def go():
        s = _store(tmp_path)
        await s.init()
        staging = s.artifacts_dir / "_staging" / "abc123"
        staging.mkdir(parents=True, exist_ok=True)
        in_flight = staging / "model.step"
        in_flight.write_text("half a generation")

        assert await s.sweep_orphans(dry_run=True) == []
        assert in_flight.exists()

    asyncio.run(go())


def test_crash_debris_beyond_the_grace_is_still_reported(tmp_path):
    """The grace protects work in flight, not litter."""

    async def go():
        s = _store(tmp_path)
        await s.init()
        staging = s.artifacts_dir / "_staging" / "dead-run"
        staging.mkdir(parents=True, exist_ok=True)
        left = staging / "model.step"
        left.write_text("what a killed run left")
        old = time.time() - 30 * 86400
        os.utime(left, (old, old))

        assert str(left) in await s.sweep_orphans(dry_run=True)

    asyncio.run(go())


class TestTheModeIsTheGuaranteeThatNothingDeletes:
    """The switch is what makes "it only reports" true rather than intended."""

    def test_no_value_asks_for_deletion(self):
        assert "delete" not in SWEEP_MODES
        assert set(SWEEP_MODES) == {"report", "off"}

    @pytest.mark.parametrize("configured", ["delete", "yes", "true", "1", "nonsense"])
    def test_a_value_it_does_not_know_stops_the_process_starting(self, configured):
        """Including `delete` — someone will try it, and it must not be read as
        anything at all, least of all as the nearest thing that does run."""
        with pytest.raises(ValidationError):
            Settings(sweep_on_start=configured)

    @pytest.mark.parametrize("configured", ["  OFF ", "Report", "off"])
    def test_a_mode_is_case_folded_and_trimmed(self, configured):
        assert Settings(sweep_on_start=configured).sweep_on_start == configured.strip().lower()

    def test_an_empty_value_reads_as_unset(self):
        """Which is how a compose file spells a default: `${CADLESS_SWEEP_ON_START:-}`."""
        assert Settings(sweep_on_start="").sweep_on_start == "report"


class TestTheStartupReport:
    """A start looks, says what it found, and removes nothing."""

    @staticmethod
    def _seed_litter(s):
        """An abandoned run's leftovers, old enough to be past the grace."""
        stale = s.artifacts_dir / "_staging" / "dead-run"
        stale.mkdir(parents=True, exist_ok=True)
        left = stale / "model.step"
        left.write_bytes(b"x" * 2048)
        old = time.time() - 30 * 86400
        os.utime(left, (old, old))
        return left

    def test_it_reports_what_it_found_and_deletes_nothing(self, tmp_path, monkeypatch, caplog):
        s = _store(tmp_path)
        asyncio.run(s.init())
        left = self._seed_litter(s)
        monkeypatch.setattr(settings, "sweep_on_start", "report")

        with caplog.at_level(logging.INFO):
            with TestClient(create_app(store=s)):
                pass

        assert left.exists(), "a report must not delete"
        said = [r.getMessage() for r in caplog.records]
        assert any("unreferenced file" in m for m in said), said
        # The size matters as much as the count: "12 files" is not a reason to
        # act, and "12 files holding 4 GB" is.
        assert any("MB" in m for m in said), said

    def test_off_does_not_look(self, tmp_path, monkeypatch, caplog):
        s = _store(tmp_path)
        asyncio.run(s.init())
        self._seed_litter(s)
        monkeypatch.setattr(settings, "sweep_on_start", "off")

        with caplog.at_level(logging.INFO):
            with TestClient(create_app(store=s)):
                pass

        assert not any("artifact sweep" in r.getMessage() for r in caplog.records)

    def test_the_app_still_starts_when_the_sweep_raises(self, tmp_path, monkeypatch):
        """A report that cannot be produced is not a reason to refuse to serve."""
        s = _store(tmp_path)
        asyncio.run(s.init())
        monkeypatch.setattr(settings, "sweep_on_start", "report")

        async def boom(**_kwargs):
            raise OSError("the disk went away")

        monkeypatch.setattr(s, "sweep_orphans", boom)

        with TestClient(create_app(store=s)) as client:
            assert client.get("/health").status_code == 200

    def test_the_app_tells_the_sweep_what_a_version_keeps_without_a_row(
        self, tmp_path, monkeypatch, caplog
    ):
        """Driven through the app, because the wiring is the thing at risk.

        Asserting the store rule directly leaves the one line that carries the
        names from the app into the sweep covered by nothing — and losing it
        ships green while every operator's sliced job starts being counted as
        litter.
        """
        from backend.routers.printing import GCODE_NAME

        s = _store(tmp_path)

        async def seed():
            await s.init()
            p = await s.create_project("P")
            v = await s.add_version(p.id, "x", "result=1", ok=True)
            d = Path(s.version_artifact_dir(v.id))
            stl = d / "model.stl"
            stl.write_text("mesh")
            await s.add_artifact(v.id, "stl", str(stl))
            job = d / GCODE_NAME
            job.write_bytes(b"G28")
            old = time.time() - 30 * 86400
            os.utime(job, (old, old))  # old enough that only the keep-rule saves it
            return job

        job = asyncio.run(seed())
        monkeypatch.setattr(settings, "sweep_on_start", "report")

        with caplog.at_level(logging.INFO):
            with TestClient(create_app(store=s)):
                pass

        said = [r.getMessage() for r in caplog.records if "artifact sweep" in r.getMessage()]
        assert said, "the report did not run"
        assert "nothing unreferenced" in said[0], said
        assert job.exists()

    def test_the_names_come_from_the_modules_that_own_them(self):
        """Not repeated here: a second copy of a filename is a thing to keep in step."""
        from backend.app import _files_a_version_keeps_without_a_row
        from backend.routers.printing import GCODE_NAME
        from cadless.slicing import PART_SUFFIX

        assert _files_a_version_keeps_without_a_row() == (GCODE_NAME, GCODE_NAME + PART_SUFFIX)


def test_grace_window_protects_recent_orphans(tmp_path):
    async def go():
        s = _store(tmp_path)
        await s.init()
        s.artifacts_dir.mkdir(parents=True, exist_ok=True)
        fresh = s.artifacts_dir / "fresh.glb"
        fresh.write_bytes(b"x")
        # 1-day grace: a just-created orphan is protected
        assert await s.sweep_orphans(grace_days=1) == []
        assert fresh.exists()
        # backdate mtime beyond grace -> now eligible
        old = time.time() - 2 * 86400
        os.utime(fresh, (old, old))
        assert str(fresh) in await s.sweep_orphans(grace_days=1)

    asyncio.run(go())
