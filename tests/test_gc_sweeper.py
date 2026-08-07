"""Artifact GC sweeper tests."""

import asyncio
import os
import time
from pathlib import Path

from cadless.store import Store


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

        # dry-run lists the orphan but deletes nothing
        listed = await s.sweep_orphans(dry_run=True)
        assert str(orphan) in listed
        assert orphan.exists() and referenced.exists()

        # real sweep removes the orphan, keeps the referenced file
        deleted = await s.sweep_orphans()
        assert str(orphan) in deleted
        assert not orphan.exists()
        assert referenced.exists()

    asyncio.run(go())


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
