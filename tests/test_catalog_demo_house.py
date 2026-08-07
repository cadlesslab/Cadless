"""End-to-end test of the committed demo house (catalog Phase 1).

Loads the pre-baked, committed demo house into a throwaway Store. No build123d
execution required since the artifacts are committed.
"""

import asyncio
from pathlib import Path

import pytest

from cadless.catalog.ledger import Ledger
from cadless.catalog.loader import load_house
from cadless.catalog.manifest import load_manifest
from cadless.config import settings
from cadless.store import Store

# The demo house ships in-repo, so this runs by default; the guard only covers
# settings.catalog_root being pointed at content that lacks it.
DEMO = settings.house_catalog_dir / "demo-house"
pytestmark = pytest.mark.skipif(
    not (DEMO / "manifest.json").exists(),
    reason=f"demo house absent: {DEMO} (set CADLESS_CATALOG_ROOT)",
)


def test_demo_manifest_is_baked():
    manifest = load_manifest(DEMO)
    assert len(manifest.steps) == 5
    for step in manifest.steps:
        assert step.geometry.volume and step.geometry.volume > 0
        assert step.geometry.bbox and len(step.geometry.bbox) == 3
        assert "glb" in step.artifacts


def test_demo_house_loads_into_store(tmp_path):
    async def go():
        s = Store(db_path=tmp_path / "db.sqlite", artifacts_dir=tmp_path / "arts")
        await s.init()
        led = Ledger(tmp_path / "ledger.json")

        pid = await load_house(s, led, DEMO)
        assert pid is not None

        versions = await s.list_versions(pid)
        assert len(versions) == 5
        assert (await s.get_project(pid)).current_version_id == versions[-1].id
        for v in versions:
            glb = await s.get_artifact(v.id, "glb")
            assert glb is not None and Path(glb.path).stat().st_size > 0

    asyncio.run(go())
