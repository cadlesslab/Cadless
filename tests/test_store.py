"""Persistence layer tests. Uses asyncio.run + temp dirs (no plugin)."""

import asyncio
from pathlib import Path

from cadless.store import LEGACY_PUBLISH_PLUGIN, Store


def _store(tmp_path: Path) -> Store:
    return Store(db_path=tmp_path / "db.sqlite", artifacts_dir=tmp_path / "artifacts")


def run(coro):
    return asyncio.run(coro)


def test_project_crud(tmp_path):
    async def go():
        s = _store(tmp_path)
        await s.init()
        p = await s.create_project("Bracket study")
        assert p.id and p.name == "Bracket study"
        assert [x.id for x in await s.list_projects()] == [p.id]
        assert (await s.get_project(p.id)).name == "Bracket study"
        renamed = await s.rename_project(p.id, "Bracket v2")
        assert renamed.name == "Bracket v2"
        assert await s.rename_project(9999, "x") is None
        assert await s.delete_project(p.id) is True
        assert await s.get_project(p.id) is None
        assert await s.delete_project(p.id) is False

    run(go())


def test_plugin_data_round_trips(tmp_path):
    """What a build records against a project survives, keyed by that build."""

    async def go():
        s = _store(tmp_path)
        await s.init()
        p = await s.create_project("Bracket study")
        # Never written and written empty are different answers.
        assert await s.plugin_data(p.id, "depot") is None
        record = {"slug": "bracket-study", "meta": {"license": "CC-BY-4.0"}}
        assert await s.record_plugin_data(p.id, "depot", record) is True
        assert await s.plugin_data(p.id, "depot") == record
        # Replaces rather than merges: the record describes the last thing done.
        await s.record_plugin_data(p.id, "depot", {"slug": "bracket-study-2"})
        assert await s.plugin_data(p.id, "depot") == {"slug": "bracket-study-2"}
        # And there is no such project rather than a silently created row.
        assert await s.record_plugin_data(9999, "depot", {}) is False

    run(go())


def test_plugin_data_is_read_for_many_projects_at_once(tmp_path):
    """A listing asks about every project it shows, and a read per row is a
    connection per row. Absent records are absent rather than None, so a caller
    still tells "never written" from "written empty"."""

    async def go():
        s = _store(tmp_path)
        await s.init()
        one = await s.create_project("One")
        two = await s.create_project("Two")
        three = await s.create_project("Three")
        await s.record_plugin_data(one.id, "depot", {"slug": "a"})
        await s.record_plugin_data(three.id, "depot", {})
        # And another build's rows are not mixed in.
        await s.record_plugin_data(two.id, "atlas", {"slug": "b"})

        found = await s.plugin_data_for([one.id, two.id, three.id], "depot")

        assert found == {one.id: {"slug": "a"}, three.id: {}}
        assert await s.plugin_data_for([], "depot") == {}

    run(go())


def test_recording_against_a_project_deleted_mid_flight_is_refused_not_raised(tmp_path):
    """The caller most likely to be here is recording a publish that already
    happened. Reporting that as an error sends the next attempt into a
    duplicate, so "there is no such project" has to be an answer rather than an
    exception — which means the check and the write must be one statement."""

    async def go():
        s = _store(tmp_path)
        await s.init()
        p = await s.create_project("Bracket study")
        await s.delete_project(p.id)
        assert await s.record_plugin_data(p.id, "depot", {"slug": "one"}) is False

    run(go())


def test_one_builds_record_does_not_disturb_anothers(tmp_path):
    """The whole reason it is keyed rather than one blob on the row."""

    async def go():
        s = _store(tmp_path)
        await s.init()
        p = await s.create_project("Bracket study")
        await s.record_plugin_data(p.id, "depot", {"slug": "one"})
        await s.record_plugin_data(p.id, "atlas", {"slug": "two"})
        assert await s.plugin_data(p.id, "depot") == {"slug": "one"}
        assert await s.plugin_data(p.id, "atlas") == {"slug": "two"}

    run(go())


def test_deleting_a_project_takes_its_plugin_records_with_it(tmp_path):
    """A reinstalled build must not find rows pointing at projects that are gone."""

    async def go():
        s = _store(tmp_path)
        await s.init()
        p = await s.create_project("Bracket study")
        await s.record_plugin_data(p.id, "depot", {"slug": "one"})
        await s.delete_project(p.id)
        assert await s.plugin_data(p.id, "depot") is None

    run(go())


def test_versions_and_current(tmp_path):
    async def go():
        s = _store(tmp_path)
        await s.init()
        p = await s.create_project("P")
        v1 = await s.add_version(
            p.id, "a cube", "result = Box(1,1,1)", ok=True, volume=1.0, bbox=(1, 1, 1)
        )
        v2 = await s.add_version(p.id, "a rod", None, ok=False, error="boom")
        assert [v.id for v in await s.list_versions(p.id)] == [v1.id, v2.id]
        got = await s.get_version(v1.id)
        assert got.bbox == (1, 1, 1) and got.ok is True
        assert (await s.get_version(v2.id)).ok is False
        assert await s.set_current_version(p.id, v1.id) is True
        assert (await s.get_project(p.id)).current_version_id == v1.id
        assert await s.set_current_version(p.id, 9999) is False

    run(go())


def test_candidate_version_flag_and_query(tmp_path):
    """A losing candidate row records its winning sibling and is
    retrievable via that pointer; a normal version has it NULL."""

    async def go():
        s = _store(tmp_path)
        await s.init()
        p = await s.create_project("P")
        winner = await s.add_version(p.id, "a cube", "result = Box(2,2,2)", ok=True)
        loser = await s.add_version(
            p.id, "a cube", "result = Box(3,3,3)", ok=True, candidate_of_version_id=winner.id
        )
        assert winner.candidate_of_version_id is None
        assert loser.candidate_of_version_id == winner.id
        assert (await s.get_version(loser.id)).candidate_of_version_id == winner.id
        assert [v.id for v in await s.list_candidate_versions(winner.id)] == [loser.id]
        assert await s.list_candidate_versions(loser.id) == []

    run(go())


def test_plan_step_annotation_roundtrips(tmp_path):
    """An optional plan_step annotation round-trips through the version insert; a version persisted without one is NULL and behaves as today."""

    async def go():
        s = _store(tmp_path)
        await s.init()
        p = await s.create_project("P")
        annotated = await s.add_version(p.id, "a cube", "result = Box(1,1,1)", ok=True, plan_step=2)
        plain = await s.add_version(p.id, "a rod", "result = Box(1,1,3)", ok=True)
        assert annotated.plan_step == 2
        assert plain.plan_step is None
        assert (await s.get_version(annotated.id)).plan_step == 2
        assert (await s.get_version(plain.id)).plan_step is None

    run(go())


def test_artifacts_and_blob_dir(tmp_path):
    async def go():
        s = _store(tmp_path)
        await s.init()
        p = await s.create_project("P")
        v = await s.add_version(p.id, "x", "result=1", ok=True)
        d = Path(s.version_artifact_dir(v.id))
        step = d / "model.step"
        step.write_text("ISO-10303")
        a = await s.add_artifact(v.id, "step", str(step))
        assert a.bytes == len("ISO-10303") and a.kind == "step"
        assert [x.kind for x in await s.list_artifacts(v.id)] == ["step"]
        assert (await s.get_artifact(v.id, "step")).path == str(step)
        assert await s.get_artifact(v.id, "glb") is None

    run(go())


def test_delete_cascades_versions_artifacts_and_blobs(tmp_path):
    async def go():
        s = _store(tmp_path)
        await s.init()
        p = await s.create_project("P")
        v = await s.add_version(p.id, "x", "result=1", ok=True)
        d = Path(s.version_artifact_dir(v.id))
        (d / "model.glb").write_bytes(b"glTF...")
        await s.add_artifact(v.id, "glb", str(d / "model.glb"))
        assert d.exists()
        await s.delete_project(p.id)
        # version + artifact rows gone (cascade), blob dir removed
        assert await s.get_version(v.id) is None
        assert await s.list_artifacts(v.id) == []
        assert not d.exists()

    run(go())


def test_version_persists_and_returns_parameters(tmp_path):
    async def go():
        s = _store(tmp_path)
        await s.init()
        p = await s.create_project("P")
        params = {"length": 40, "hole_dia": 6}
        v = await s.add_version(
            p.id,
            "a plate",
            "result = Box(1,1,1)",
            ok=True,
            volume=1.0,
            bbox=(1, 1, 1),
            parameters=params,
        )
        assert v.parameters == params
        # round-trips through the DB
        assert (await s.get_version(v.id)).parameters == params
        assert (await s.list_versions(p.id))[0].parameters == params
        # default is an empty dict, not None
        v2 = await s.add_version(p.id, "x", "result=1", ok=True)
        assert (await s.get_version(v2.id)).parameters == {}

    run(go())


def test_version_persists_parent_lineage(tmp_path):
    async def go():
        s = _store(tmp_path)
        await s.init()
        p = await s.create_project("P")
        parent = await s.add_version(p.id, "a cube", "result = Box(5,5,5)", ok=True)
        child = await s.add_version(
            p.id, "make it bigger", "result = Box(9,9,9)", ok=True, parent_version_id=parent.id
        )
        assert child.parent_version_id == parent.id
        assert (await s.get_version(child.id)).parent_version_id == parent.id
        # a fresh (non-refined) version has no parent
        assert parent.parent_version_id is None

    run(go())


def test_migration_adds_parameters_column_to_legacy_db(tmp_path):
    """A DB created without parameters_json gains the column on init()."""
    import sqlite3

    db = tmp_path / "db.sqlite"
    legacy = sqlite3.connect(db)
    legacy.executescript(
        "CREATE TABLE projects (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,"
        " created_at TEXT NOT NULL, updated_at TEXT NOT NULL, current_version_id INTEGER);"
        "CREATE TABLE script_versions (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " project_id INTEGER NOT NULL, prompt TEXT NOT NULL, code TEXT, ok INTEGER NOT NULL,"
        " error TEXT, volume REAL, bbox_json TEXT, created_at TEXT NOT NULL);"
        "INSERT INTO projects(name,created_at,updated_at) VALUES ('old','t','t');"
        "INSERT INTO script_versions(project_id,prompt,ok,created_at)"
        " VALUES (1,'legacy',1,'t');"
    )
    legacy.commit()
    legacy.close()

    async def go():
        s = Store(db_path=db, artifacts_dir=tmp_path / "artifacts")
        await s.init()  # must not error; back-fills the column
        v = (await s.list_versions(1))[0]
        # legacy row reads cleanly with defaults for both added columns
        assert v.prompt == "legacy" and v.parameters == {} and v.parent_version_id is None
        # and new params/lineage-bearing versions persist fine afterwards
        nv = await s.add_version(
            1, "new", "result=1", ok=True, parameters={"a": 1}, parent_version_id=v.id
        )
        got = await s.get_version(nv.id)
        assert got.parameters == {"a": 1} and got.parent_version_id == v.id

    run(go())


def _publishing_era_db(tmp_path, *, meta='{"license": "MIT"}'):
    """A database from when publishing was the engine's own, with one publish in it.

    Built rather than committed, which is the pattern the older migration test
    here already used: a checked-in binary would have to be regenerated by hand
    every time the schema moved, and would stop being evidence the moment
    somebody forgot.
    """
    import sqlite3

    db = tmp_path / "db.sqlite"
    legacy = sqlite3.connect(db)
    legacy.executescript(
        "CREATE TABLE projects (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,"
        " created_at TEXT NOT NULL, updated_at TEXT NOT NULL, current_version_id INTEGER,"
        " branched_from_version_id INTEGER, derived_from_project_id INTEGER,"
        " catalog_item_id TEXT, published_slug TEXT, publish_meta_json TEXT);"
        "INSERT INTO projects(name,created_at,updated_at,published_slug,publish_meta_json)"
        f" VALUES ('published','t','t','bracket-study','{meta}');"
        "INSERT INTO projects(name,created_at,updated_at) VALUES ('never published','t','t');"
    )
    legacy.commit()
    legacy.close()
    return db


def test_a_publish_recorded_before_this_change_is_still_reported(tmp_path):
    """The acceptance the whole migration exists for, against a real older database."""
    db = _publishing_era_db(tmp_path)

    async def go():
        s = Store(db_path=db, artifacts_dir=tmp_path / "artifacts")
        await s.init()
        assert await s.plugin_data(1, LEGACY_PUBLISH_PLUGIN) == {
            "slug": "bracket-study",
            "meta": {"license": "MIT"},
        }
        # And a project that was never published still has nothing recorded,
        # rather than an empty record that would read as a first publish done.
        assert await s.plugin_data(2, LEGACY_PUBLISH_PLUGIN) is None

    run(go())


def test_carrying_the_old_publish_forward_does_not_undo_a_later_one(tmp_path):
    """It runs on every init(), so it has to lose to whatever has happened since."""
    db = _publishing_era_db(tmp_path)

    async def go():
        s = Store(db_path=db, artifacts_dir=tmp_path / "artifacts")
        await s.init()
        await s.record_plugin_data(1, LEGACY_PUBLISH_PLUGIN, {"slug": "renamed"})
        await s.init()  # a restart
        assert await s.plugin_data(1, LEGACY_PUBLISH_PLUGIN) == {"slug": "renamed"}

    run(go())


def test_an_unreadable_publish_record_still_carries_its_address_forward(tmp_path):
    """The address cannot be recovered from anywhere else; the meta can be re-chosen."""
    db = _publishing_era_db(tmp_path, meta="not json at all")

    async def go():
        s = Store(db_path=db, artifacts_dir=tmp_path / "artifacts")
        await s.init()
        assert await s.plugin_data(1, LEGACY_PUBLISH_PLUGIN) == {"slug": "bracket-study"}

    run(go())


def test_a_database_predating_the_publish_columns_still_opens(tmp_path):
    """Nothing to carry forward is not an error, and there is no column to read."""
    import sqlite3

    db = tmp_path / "db.sqlite"
    legacy = sqlite3.connect(db)
    legacy.executescript(
        "CREATE TABLE projects (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,"
        " created_at TEXT NOT NULL, updated_at TEXT NOT NULL, current_version_id INTEGER);"
        "INSERT INTO projects(name,created_at,updated_at) VALUES ('old','t','t');"
    )
    legacy.commit()
    legacy.close()

    async def go():
        s = Store(db_path=db, artifacts_dir=tmp_path / "artifacts")
        await s.init()
        await s.init()  # second init must not fail on the already-added columns
        assert (await s.get_project(1)).name == "old"
        assert await s.plugin_data(1, LEGACY_PUBLISH_PLUGIN) is None
        assert await s.record_plugin_data(1, LEGACY_PUBLISH_PLUGIN, {"slug": "old"}) is True

    run(go())


def test_init_is_idempotent(tmp_path):
    async def go():
        s = _store(tmp_path)
        await s.init()
        await s.init()  # second call must not error
        assert await s.list_projects() == []

    run(go())


# ---- chat sessions + messages -------------------------------


def test_session_created_with_project_and_is_unique(tmp_path):
    async def go():
        s = _store(tmp_path)
        await s.init()
        p = await s.create_project("P")
        # a session exists for a freshly created project
        sess = await s.get_or_create_session(p.id)
        assert sess.id and sess.project_id == p.id
        assert sess.created_at and sess.updated_at
        # get_or_create is idempotent -- one session per project
        again = await s.get_or_create_session(p.id)
        assert again.id == sess.id

    run(go())


def test_add_message_assigns_monotonic_seq(tmp_path):
    async def go():
        s = _store(tmp_path)
        await s.init()
        p = await s.create_project("P")
        sess = await s.get_or_create_session(p.id)
        m1 = await s.add_message(sess.id, "user", "make a cube")
        m2 = await s.add_message(sess.id, "assistant", "done")
        m3 = await s.add_message(sess.id, "system", "note")
        assert [m1.seq, m2.seq, m3.seq] == [1, 2, 3]
        assert m1.status == "ok" and m1.role == "user" and m1.content == "make a cube"

    run(go())


def test_list_messages_ordered_by_seq(tmp_path):
    async def go():
        s = _store(tmp_path)
        await s.init()
        p = await s.create_project("P")
        sess = await s.get_or_create_session(p.id)
        await s.add_message(sess.id, "user", "one")
        await s.add_message(sess.id, "assistant", "two")
        await s.add_message(sess.id, "user", "three")
        msgs = await s.list_messages(sess.id)
        assert [m.seq for m in msgs] == [1, 2, 3]
        assert [m.content for m in msgs] == ["one", "two", "three"]

    run(go())


def test_add_message_with_status_error_and_version(tmp_path):
    async def go():
        s = _store(tmp_path)
        await s.init()
        p = await s.create_project("P")
        v = await s.add_version(p.id, "x", "result=1", ok=True)
        sess = await s.get_or_create_session(p.id)
        m = await s.add_message(sess.id, "assistant", None, status="pending")
        assert m.status == "pending" and m.content is None
        assert m.error is None and m.version_id is None
        m2 = await s.add_message(sess.id, "assistant", "ok", status="ok", version_id=v.id)
        assert m2.version_id == v.id
        m3 = await s.add_message(sess.id, "assistant", None, status="error", error="boom")
        assert m3.status == "error" and m3.error == "boom"

    run(go())


def test_update_message_patches_fields_and_round_trips(tmp_path):
    async def go():
        s = _store(tmp_path)
        await s.init()
        p = await s.create_project("P")
        v = await s.add_version(p.id, "x", "result=1", ok=True)
        sess = await s.get_or_create_session(p.id)
        m = await s.add_message(sess.id, "assistant", None, status="pending")
        updated = await s.update_message(m.id, status="ok", content="here it is", version_id=v.id)
        assert updated is not None
        assert updated.status == "ok" and updated.content == "here it is"
        assert updated.version_id == v.id and updated.seq == m.seq
        # persisted
        msgs = await s.list_messages(sess.id)
        assert msgs[0].status == "ok" and msgs[0].content == "here it is"
        # partial update leaves other fields untouched
        again = await s.update_message(m.id, error="late error")
        assert again.error == "late error" and again.content == "here it is"
        # unknown id -> None
        assert await s.update_message(999999, status="ok") is None

    run(go())


def test_session_and_messages_cascade_delete_with_project(tmp_path):
    async def go():
        s = _store(tmp_path)
        await s.init()
        p = await s.create_project("P")
        sess = await s.get_or_create_session(p.id)
        await s.add_message(sess.id, "user", "hi")
        await s.delete_project(p.id)
        # messages gone, session gone
        assert await s.list_messages(sess.id) == []
        # re-create under a new project -> a brand new session id space, old one absent
        import sqlite3

        con = sqlite3.connect(s.db_path)
        n_sess = con.execute(
            "SELECT COUNT(*) FROM chat_sessions WHERE id=?", (sess.id,)
        ).fetchone()[0]
        n_msg = con.execute(
            "SELECT COUNT(*) FROM chat_messages WHERE session_id=?", (sess.id,)
        ).fetchone()[0]
        con.close()
        assert n_sess == 0 and n_msg == 0

    run(go())


def test_message_version_id_set_null_on_version_delete(tmp_path):
    async def go():
        s = _store(tmp_path)
        await s.init()
        p = await s.create_project("P")
        v = await s.add_version(p.id, "x", "result=1", ok=True)
        sess = await s.get_or_create_session(p.id)
        m = await s.add_message(sess.id, "assistant", "made it", version_id=v.id)
        # delete the version row directly; message survives with version_id NULLed
        import sqlite3

        con = sqlite3.connect(s.db_path)
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("DELETE FROM script_versions WHERE id=?", (v.id,))
        con.commit()
        con.close()
        msgs = await s.list_messages(sess.id)
        assert len(msgs) == 1 and msgs[0].id == m.id and msgs[0].version_id is None

    run(go())


# ---- rich block persistence ---------------------------------


def test_add_message_with_blocks_round_trips(tmp_path):
    """Neutral content blocks survive a write/read incl. provider/provider_raw."""
    from cadless.llm.types import ContentBlock

    async def go():
        s = _store(tmp_path)
        await s.init()
        p = await s.create_project("P")
        sess = await s.get_or_create_session(p.id)
        blocks = [
            ContentBlock.of_thinking(
                "let me think",
                provider="bedrock",
                provider_raw={"type": "thinking", "signature": "abc"},
            ),
            ContentBlock.of_text("here you go", provider="bedrock"),
            ContentBlock.of_tool_use(
                id="t1",
                name="run_code",
                input={"code": "Box(1,1,1)"},
                provider="bedrock",
                provider_raw={"raw": True},
            ),
        ]
        m = await s.add_message(sess.id, "assistant", "here you go", blocks=blocks)
        assert [b.model_dump() for b in m.blocks] == [b.model_dump() for b in blocks]
        # round-trips through the DB
        msgs = await s.list_messages(sess.id)
        got = msgs[0].blocks
        assert [b.model_dump() for b in got] == [b.model_dump() for b in blocks]
        # provenance preserved
        assert got[0].provider == "bedrock"
        assert got[0].provider_raw == {"type": "thinking", "signature": "abc"}
        assert got[2].kind == "tool_use" and got[2].input == {"code": "Box(1,1,1)"}

    run(go())


def test_message_without_blocks_has_empty_blocks(tmp_path):
    """A message stored without blocks reads back with an empty block list."""

    async def go():
        s = _store(tmp_path)
        await s.init()
        p = await s.create_project("P")
        sess = await s.get_or_create_session(p.id)
        m = await s.add_message(sess.id, "user", "make a cube")
        assert m.blocks == []
        assert (await s.list_messages(sess.id))[0].blocks == []

    run(go())


def test_update_message_sets_blocks(tmp_path):
    """update_message can attach blocks to an existing message."""
    from cadless.llm.types import ContentBlock

    async def go():
        s = _store(tmp_path)
        await s.init()
        p = await s.create_project("P")
        sess = await s.get_or_create_session(p.id)
        m = await s.add_message(sess.id, "assistant", None, status="pending")
        assert m.blocks == []
        blocks = [ContentBlock.of_text("done")]
        updated = await s.update_message(m.id, status="ok", content="done", blocks=blocks)
        assert updated.status == "ok" and updated.content == "done"
        assert [b.model_dump() for b in updated.blocks] == [b.model_dump() for b in blocks]
        # persisted, and a partial update leaves blocks untouched
        again = await s.update_message(m.id, error="oops")
        assert [b.model_dump() for b in again.blocks] == [b.model_dump() for b in blocks]

    run(go())


def test_migration_adds_blocks_json_column_to_legacy_db(tmp_path):
    """A chat_messages table created without blocks_json gains the column on init()."""
    import sqlite3

    from cadless.llm.types import ContentBlock

    db = tmp_path / "db.sqlite"
    legacy = sqlite3.connect(db)
    legacy.executescript(
        "CREATE TABLE projects (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,"
        " created_at TEXT NOT NULL, updated_at TEXT NOT NULL, current_version_id INTEGER);"
        "CREATE TABLE chat_sessions (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " project_id INTEGER NOT NULL UNIQUE, created_at TEXT NOT NULL,"
        " updated_at TEXT NOT NULL);"
        "CREATE TABLE chat_messages (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " session_id INTEGER NOT NULL, seq INTEGER NOT NULL, role TEXT NOT NULL,"
        " content TEXT, status TEXT NOT NULL DEFAULT 'ok', error TEXT,"
        " version_id INTEGER, created_at TEXT NOT NULL);"
        "INSERT INTO projects(name,created_at,updated_at) VALUES ('old','t','t');"
        "INSERT INTO chat_sessions(project_id,created_at,updated_at) VALUES (1,'t','t');"
        "INSERT INTO chat_messages(session_id,seq,role,content,status,created_at)"
        " VALUES (1,1,'user','legacy msg','ok','t');"
    )
    legacy.commit()
    legacy.close()

    async def go():
        s = Store(db_path=db, artifacts_dir=tmp_path / "artifacts")
        await s.init()  # must not error; back-fills the column
        sess = await s.get_or_create_session(1)
        msgs = await s.list_messages(sess.id)
        # legacy row reads cleanly: null blocks_json -> empty block list
        assert msgs[0].content == "legacy msg" and msgs[0].blocks == []
        # new block-bearing messages persist fine afterwards
        nm = await s.add_message(sess.id, "assistant", "ok", blocks=[ContentBlock.of_text("ok")])
        got = (await s.list_messages(sess.id))[1]
        assert [b.model_dump() for b in got.blocks] == [b.model_dump() for b in nm.blocks]

    run(go())


def test_migration_seeds_one_session_per_existing_project(tmp_path):
    """A legacy DB with projects but no chat tables gets one session per project."""
    import sqlite3

    db = tmp_path / "db.sqlite"
    legacy = sqlite3.connect(db)
    legacy.executescript(
        "CREATE TABLE projects (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,"
        " created_at TEXT NOT NULL, updated_at TEXT NOT NULL, current_version_id INTEGER);"
        "CREATE TABLE script_versions (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " project_id INTEGER NOT NULL, prompt TEXT NOT NULL, code TEXT, ok INTEGER NOT NULL,"
        " error TEXT, volume REAL, bbox_json TEXT, created_at TEXT NOT NULL);"
        "INSERT INTO projects(name,created_at,updated_at) VALUES ('a','t','t');"
        "INSERT INTO projects(name,created_at,updated_at) VALUES ('b','t','t');"
    )
    legacy.commit()
    legacy.close()

    async def go():
        s = Store(db_path=db, artifacts_dir=tmp_path / "artifacts")
        await s.init()
        # exactly one session per existing project, no duplicates on a second init
        await s.init()
        import sqlite3 as _sq

        con = _sq.connect(db)
        rows = con.execute(
            "SELECT project_id, COUNT(*) FROM chat_sessions GROUP BY project_id"
        ).fetchall()
        con.close()
        assert sorted(rows) == [(1, 1), (2, 1)]
        # the seeded sessions are reachable via get_or_create_session
        s1 = await s.get_or_create_session(1)
        s2 = await s.get_or_create_session(2)
        assert s1.project_id == 1 and s2.project_id == 2 and s1.id != s2.id

    run(go())


def test_branch_project_seeds_new_line_and_records_origin(tmp_path):
    """branch_project forks a version into a fresh project."""

    async def go():
        s = _store(tmp_path)
        await s.init()
        origin = await s.create_project("Origin")
        v1 = await s.add_version(
            origin.id,
            "a cube",
            "result = Box(1,1,1)",
            ok=True,
            volume=1.0,
            bbox=(1, 1, 1),
            parameters={"size": 1},
        )
        await s.add_version(origin.id, "v2", "result = Box(2,2,2)", ok=True)

        branched = await s.branch_project(v1.id, name="Fork")
        assert branched.id != origin.id
        assert branched.branched_from_version_id == v1.id

        # new line seeded with exactly one version == the selected version's model
        seeded = await s.list_versions(branched.id)
        assert len(seeded) == 1
        assert seeded[0].code == "result = Box(1,1,1)"
        assert seeded[0].parameters == {"size": 1}
        assert seeded[0].project_id == branched.id  # owned by the new line
        # the seeded version is the new line's current model
        assert (await s.get_project(branched.id)).current_version_id == seeded[0].id
        # it has its own chat session (1:1 invariant)
        sess = await s.get_or_create_session(branched.id)
        assert sess.project_id == branched.id

        # original untouched: still two versions, current unchanged
        assert len(await s.list_versions(origin.id)) == 2

        # unknown version -> None
        assert await s.branch_project(9999, name="x") is None

    run(go())


# ---- KB + sqlite vector index -------------------------------


def test_cosine_similarity_pure_helper():
    """The brute-force similarity math is a unit-testable pure function."""
    from cadless.store import cosine_similarity

    # identical direction -> 1.0; orthogonal -> 0.0; opposite -> -1.0
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == 0.0
    assert cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == -1.0
    # magnitude-invariant (cosine, not dot)
    assert abs(cosine_similarity([2.0, 0.0], [5.0, 0.0]) - 1.0) < 1e-9
    # zero vector is defined (no division-by-zero) -> 0.0
    assert cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0


def test_add_kb_entry_round_trips_all_fields(tmp_path):
    """A KB entry persists NL intent, code, params, geometry sig, provenance, vector."""

    async def go():
        s = _store(tmp_path)
        await s.init()
        p = await s.create_project("P")
        v = await s.add_version(p.id, "a plate", "result = Box(40, 20, 5)", ok=True)
        params = {"length": 40, "width": 20, "thickness": 5}
        signature = {
            "bbox": [40, 20, 5],
            "volume": 4000.0,
            "feature_tags": ["plate", "rectangular"],
        }
        provenance = {
            "project_id": p.id,
            "version_id": v.id,
            "metrics": {"render_ok": True, "score": 0.9},
        }
        embedding = [0.1, 0.2, 0.3, 0.4]
        e = await s.add_kb_entry(
            nl_intent="a 40x20x5 plate",
            code="result = Box(40, 20, 5)",
            embedding=embedding,
            params=params,
            geometry_signature=signature,
            provenance=provenance,
        )
        assert e.id
        # round-trips through the DB via get
        got = await s.get_kb_entry(e.id)
        assert got.nl_intent == "a 40x20x5 plate"
        assert got.code == "result = Box(40, 20, 5)"
        assert got.params == params
        assert got.geometry_signature == signature
        assert got.provenance == provenance
        assert got.embedding == embedding
        assert got.created_at
        # and via list
        listed = await s.list_kb_entries()
        assert [x.id for x in listed] == [e.id]
        assert listed[0].params == params
        # unknown id -> None
        assert await s.get_kb_entry(999999) is None

    run(go())


def test_query_kb_by_vector_returns_nearest_first(tmp_path):
    """Top-k similarity query orders results by descending cosine similarity."""

    async def go():
        s = _store(tmp_path)
        await s.init()
        near = await s.add_kb_entry(nl_intent="near", code="a", embedding=[1.0, 0.0, 0.0])
        mid = await s.add_kb_entry(nl_intent="mid", code="b", embedding=[0.7, 0.7, 0.0])
        far = await s.add_kb_entry(nl_intent="far", code="c", embedding=[0.0, 0.0, 1.0])
        results = await s.query_kb_by_vector([1.0, 0.0, 0.0], top_k=3)
        # ordered nearest-first; each result carries (entry, score)
        assert [r[0].id for r in results] == [near.id, mid.id, far.id]
        assert results[0][1] > results[1][1] > results[2][1]
        # top_k truncates
        top1 = await s.query_kb_by_vector([1.0, 0.0, 0.0], top_k=1)
        assert len(top1) == 1 and top1[0][0].id == near.id

    run(go())


def test_query_kb_uses_deterministic_fake_embeddings(tmp_path):
    """End-to-end with the offline fake provider: the matching intent ranks first."""
    from cadless.config import settings as base_settings
    from cadless.llm.providers.fake import FakeChatProvider

    async def go():
        s = _store(tmp_path)
        await s.init()
        cfg = base_settings.model_copy(update={"embed_dimensions": 64})
        provider = FakeChatProvider(config=cfg)
        intents = ["a cube", "a cylinder", "a sphere"]
        ids = {}
        for intent in intents:
            e = await s.add_kb_entry(
                nl_intent=intent, code=f"# {intent}", embedding=provider.embed(intent)
            )
            ids[intent] = e.id
        # querying with the same text's embedding returns that entry first
        results = await s.query_kb_by_vector(provider.embed("a cylinder"), top_k=3)
        assert results[0][0].id == ids["a cylinder"]

    run(go())


def test_kb_is_cross_project_and_account_scoped(tmp_path):
    """KB rows are shared across projects: an entry from A is retrievable in a B query."""

    async def go():
        s = _store(tmp_path)
        await s.init()
        proj_a = await s.create_project("A")
        proj_b = await s.create_project("B")
        va = await s.add_version(proj_a.id, "x", "result=1", ok=True)
        entry_a = await s.add_kb_entry(
            nl_intent="bracket from project A",
            code="result = Box(1,1,1)",
            embedding=[1.0, 0.0, 0.0],
            provenance={"project_id": proj_a.id, "version_id": va.id},
        )
        # a query made while working in project B retrieves the project-A entry
        results = await s.query_kb_by_vector([1.0, 0.0, 0.0], top_k=5)
        assert entry_a.id in [r[0].id for r in results]
        # provenance still records the originating project
        got = await s.get_kb_entry(entry_a.id)
        assert got.provenance["project_id"] == proj_a.id
        # deleting project A does NOT remove the shared KB entry (cross-project)
        await s.delete_project(proj_a.id)
        assert await s.get_kb_entry(entry_a.id) is not None
        assert await s.get_project(proj_b.id) is not None

    run(go())


def test_kb_entry_defaults_are_empty_not_none(tmp_path):
    """Optional JSON fields default to empty dict and round-trip as empty dict."""

    async def go():
        s = _store(tmp_path)
        await s.init()
        e = await s.add_kb_entry(nl_intent="minimal", code="result=1", embedding=[0.5, 0.5])
        got = await s.get_kb_entry(e.id)
        assert got.params == {} and got.geometry_signature == {}
        assert got.provenance == {}

    run(go())


def test_thumbnail_version_ids_finds_the_version_that_holds_the_thumbnail(tmp_path):
    """The map answers "which version carries this project's thumbnail", which is
    not the same as its current version once the current pointer moves."""

    async def go():
        s = _store(tmp_path)
        await s.init()
        p = await s.create_project("Item")
        baked = await s.add_version(p.id, "step 1", "result=1", ok=True)
        later = await s.add_version(p.id, "step 2", "result=2", ok=True)
        thumb = Path(s.version_artifact_dir(baked.id)) / "thumbnail.png"
        thumb.write_bytes(b"\x89PNG")
        await s.add_artifact(baked.id, "thumbnail", str(thumb))
        await s.set_current_version(p.id, later.id)

        assert await s.thumbnail_version_ids([p.id]) == {p.id: baked.id}
        # a project with no thumbnail is simply absent, never a null entry
        other = await s.create_project("No thumb")
        assert await s.thumbnail_version_ids([p.id, other.id]) == {p.id: baked.id}
        assert await s.thumbnail_version_ids([]) == {}

    run(go())


def test_thumbnail_version_ids_spans_chunk_boundaries(tmp_path, monkeypatch):
    """The id list is sized by the whole catalog ledger, so the query is chunked.
    Every project must still come back when the list crosses a chunk boundary."""
    import cadless.store as store_mod

    monkeypatch.setattr(store_mod, "_SQL_IN_CHUNK", 2)

    async def go():
        s = _store(tmp_path)
        await s.init()
        expected = {}
        for i in range(5):  # 5 ids over a chunk size of 2 => 3 chunks
            p = await s.create_project(f"Item {i}")
            v = await s.add_version(p.id, "step", "result=1", ok=True)
            thumb = Path(s.version_artifact_dir(v.id)) / "thumbnail.png"
            thumb.write_bytes(b"\x89PNG")
            await s.add_artifact(v.id, "thumbnail", str(thumb))
            expected[p.id] = v.id

        assert await s.thumbnail_version_ids(list(expected)) == expected
        # duplicates collapse rather than inflating the parameter count
        dupes = list(expected) + list(expected)
        assert await s.thumbnail_version_ids(dupes) == expected

    run(go())
