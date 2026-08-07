"""Deep project-clone tests (catalog Clone action)."""

import asyncio
from pathlib import Path

from cadless.store import Store


def run(coro):
    return asyncio.run(coro)


def _store(tmp_path: Path) -> Store:
    return Store(db_path=tmp_path / "db.sqlite", artifacts_dir=tmp_path / "arts")


def test_clone_copies_versions_artifacts_chat_and_current(tmp_path):
    async def go():
        s = _store(tmp_path)
        await s.init()
        p = await s.create_project("Zillow X")
        sess = await s.get_or_create_session(p.id)

        v1 = await s.add_version(p.id, "slab", "result = 1", ok=True, volume=1.0, bbox=(1, 1, 1))
        # give v1 a glb artifact on disk
        g1 = Path(s.version_artifact_dir(v1.id)) / "model.glb"
        g1.write_bytes(b"GLB-ONE")
        await s.add_artifact(v1.id, "glb", str(g1))
        await s.add_message(sess.id, "user", "make a slab")
        await s.add_message(sess.id, "assistant", "done", version_id=v1.id)

        v2 = await s.add_version(
            p.id,
            "walls",
            "result = 2",
            ok=True,
            volume=2.0,
            bbox=(1, 1, 2),
            parent_version_id=v1.id,
        )
        g2 = Path(s.version_artifact_dir(v2.id)) / "model.glb"
        g2.write_bytes(b"GLB-TWO")
        await s.add_artifact(v2.id, "glb", str(g2))
        await s.add_message(sess.id, "user", "add walls")
        await s.add_message(sess.id, "assistant", "done", version_id=v2.id)
        await s.set_current_version(p.id, v2.id)

        # a forge-loser candidate row must NOT be copied
        await s.add_version(p.id, "loser", "result = 9", ok=True, candidate_of_version_id=v2.id)

        clone = await s.clone_project(p.id, name="Zillow X (copy)")
        assert clone is not None and clone.id != p.id
        assert clone.name == "Zillow X (copy)"

        cvs = await s.list_versions(clone.id)
        assert [v.prompt for v in cvs] == ["slab", "walls"]  # candidate skipped
        # version chain remapped to the clone's own ids
        assert cvs[0].parent_version_id is None
        assert cvs[1].parent_version_id == cvs[0].id
        # current points at the cloned final version
        assert (await s.get_project(clone.id)).current_version_id == cvs[1].id

        # artifacts copied to the clone's own dirs (distinct path, same bytes)
        ca = await s.get_artifact(cvs[1].id, "glb")
        assert ca is not None
        assert ca.path != str(g2)
        assert Path(ca.path).read_bytes() == b"GLB-TWO"

        # full chat history copied, version_id remapped
        csess = await s.get_or_create_session(clone.id)
        msgs = await s.list_messages(csess.id)
        assert [m.content for m in msgs] == ["make a slab", "done", "add walls", "done"]
        assert msgs[1].version_id == cvs[0].id and msgs[3].version_id == cvs[1].id

        # source untouched
        assert len(await s.list_versions(p.id)) == 3  # 2 main + 1 candidate

    run(go())


def test_clone_records_derived_from_project_id(tmp_path):
    """Provenance (#22): a clone remembers the project it was copied from."""

    async def go():
        s = _store(tmp_path)
        await s.init()
        p = await s.create_project("Baseline")
        assert p and (await s.get_project(p.id)).derived_from_project_id is None

        clone = await s.clone_project(p.id)
        assert clone.derived_from_project_id == p.id
        assert clone.name == "Baseline (copy)"
        # Round-trips through get/list.
        assert (await s.get_project(clone.id)).derived_from_project_id == p.id

    run(go())


def test_clone_missing_project_returns_none(tmp_path):
    async def go():
        s = _store(tmp_path)
        await s.init()
        assert await s.clone_project(9999) is None

    run(go())
