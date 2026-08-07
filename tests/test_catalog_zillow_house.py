"""End-to-end test of the committed real Zillow house (authoring Phase 2).

Loads the pre-baked, committed authored house into a throwaway Store. Skips
cleanly if the house has not been authored/committed yet.
"""

import asyncio
from pathlib import Path

import pytest

from cadless.catalog.ledger import Ledger
from cadless.catalog.loader import load_house
from cadless.catalog.manifest import load_manifest
from cadless.config import settings
from cadless.store import Store

# Catalog content lives under settings.catalog_root, outside the repo.
HOUSE = settings.house_catalog_dir / "zillow-10242075"


@pytest.mark.skipif(
    not (HOUSE / "manifest.json").exists(), reason="authored Zillow house not committed"
)
def test_zillow_house_is_baked():
    manifest = load_manifest(HOUSE)
    assert len(manifest.steps) >= 4  # multi-storey -> several steps
    for step in manifest.steps:
        assert step.geometry.volume and step.geometry.volume > 0
        assert "glb" in step.artifacts


@pytest.mark.skipif(
    not (HOUSE / "manifest.json").exists(), reason="authored Zillow house not committed"
)
def test_zillow_house_loads(tmp_path):
    async def go():
        s = Store(db_path=tmp_path / "db.sqlite", artifacts_dir=tmp_path / "arts")
        await s.init()
        led = Ledger(tmp_path / "ledger.json")
        manifest = load_manifest(HOUSE)

        pid = await load_house(s, led, HOUSE)
        assert pid is not None
        versions = await s.list_versions(pid)
        assert len(versions) == len(manifest.steps)
        assert (await s.get_project(pid)).current_version_id == versions[-1].id
        for v in versions:
            glb = await s.get_artifact(v.id, "glb")
            assert glb is not None and Path(glb.path).stat().st_size > 0

    asyncio.run(go())
