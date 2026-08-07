"""Carrying a database written before the owner column existed.

The rule these hold is the one an existing installation actually cares about:
opening a database that predates ownership must not hide anybody's work. Every
row it already had belonged to the single person using it, except the catalogue
that came with the build, and that split has to survive an upgrade without
anyone being asked to do anything.

The pre-migration database is built here rather than copied from a checkout, so
these run the same on a machine that has never opened the app.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from cadless.identity import LOCAL, SYSTEM_KEY
from cadless.store import Store

# The `projects` table exactly as it stood before the owner column, with the
# table-wide unique index this change has to replace.
_OLD_SCHEMA = """
CREATE TABLE projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    current_version_id INTEGER,
    branched_from_version_id INTEGER,
    derived_from_project_id INTEGER,
    catalog_item_id TEXT
);
CREATE UNIQUE INDEX idx_projects_catalog_item_id
    ON projects(catalog_item_id) WHERE catalog_item_id IS NOT NULL;
CREATE TABLE kb_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nl_intent TEXT NOT NULL,
    code TEXT NOT NULL,
    params_json TEXT,
    signature_json TEXT,
    provenance_json TEXT,
    embedding_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def run(coro):
    return asyncio.run(coro)


def _legacy_db(path: Path) -> None:
    """A database as an installation would have left it before this change."""
    con = sqlite3.connect(path)
    try:
        con.executescript(_OLD_SCHEMA)
        con.executemany(
            "INSERT INTO projects(name, created_at, updated_at, catalog_item_id) VALUES (?,?,?,?)",
            [
                ("Bracket study", "2026-01-01", "2026-01-01", None),
                ("Shelf", "2026-01-02", "2026-01-02", None),
                ("Demo house", "2026-01-03", "2026-01-03", "house/demo"),
            ],
        )
        con.execute(
            "INSERT INTO kb_entries(nl_intent, code, embedding_json, created_at) VALUES (?,?,?,?)",
            ("a bracket", "code", "[0.1, 0.2]", "2026-01-01"),
        )
        con.commit()
    finally:
        con.close()


def _store(tmp_path: Path) -> Store:
    return Store(db_path=tmp_path / "db.sqlite", artifacts_dir=tmp_path / "artifacts")


def _indexes(path: Path) -> set[str]:
    con = sqlite3.connect(path)
    try:
        rows = con.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
    finally:
        con.close()
    return {r[0] for r in rows}


def test_an_existing_database_still_shows_every_project(tmp_path):
    """The upgrade must not hide work. This is the whole acceptance criterion."""
    _legacy_db(tmp_path / "db.sqlite")

    async def go():
        s = _store(tmp_path)
        await s.init()
        return await s.list_projects()

    projects = run(go())
    assert [p.name for p in projects] == ["Bracket study", "Shelf", "Demo house"]


def test_work_becomes_the_local_users_and_the_catalogue_becomes_the_builds(tmp_path):
    _legacy_db(tmp_path / "db.sqlite")

    async def go():
        s = _store(tmp_path)
        await s.init()
        return {p.name: p.owner for p in await s.list_projects()}

    owners = run(go())
    assert owners["Bracket study"] == LOCAL.key
    assert owners["Shelf"] == LOCAL.key
    # It came with the build, so every principal reads it and none of them owns it.
    assert owners["Demo house"] == SYSTEM_KEY


def test_legacy_knowledge_base_rows_become_the_local_users(tmp_path):
    _legacy_db(tmp_path / "db.sqlite")

    async def go():
        s = _store(tmp_path)
        await s.init()
        return await s.list_kb_entries()

    entries = run(go())
    assert [e.owner for e in entries] == [LOCAL.key]


def test_the_table_wide_unique_index_is_replaced_not_left_beside_the_new_one(tmp_path):
    # Left in place it would keep enforcing the rule this replaces, and the
    # second person importing an item would be refused as a duplicate.
    _legacy_db(tmp_path / "db.sqlite")

    async def go():
        await _store(tmp_path).init()

    run(go())
    names = _indexes(tmp_path / "db.sqlite")
    assert "idx_projects_catalog_item_id" not in names
    assert "idx_projects_owner_catalog_item_id" in names


def test_two_owners_may_hold_the_same_catalogue_item(tmp_path):
    async def go():
        s = _store(tmp_path)
        await s.init()
        await s.create_project("Demo house", catalog_item_id="house/demo", owner="user-a")
        await s.create_project("Demo house", catalog_item_id="house/demo", owner="user-b")
        return await s.list_projects()

    assert len(run(go())) == 2


def test_one_owner_may_not_hold_the_same_catalogue_item_twice(tmp_path):
    # The duplicate-import failure the index exists to prevent is still refused.
    async def go():
        s = _store(tmp_path)
        await s.init()
        await s.create_project("Demo house", catalog_item_id="house/demo", owner="user-a")
        await s.create_project("Demo house", catalog_item_id="house/demo", owner="user-a")

    with pytest.raises(sqlite3.IntegrityError):
        run(go())


def test_a_row_left_with_no_owner_is_adopted_rather_than_hidden(tmp_path):
    """A NULL owner is visible to nobody, so the migration fills it every time."""
    db = tmp_path / "db.sqlite"

    async def prepare():
        s = _store(tmp_path)
        await s.init()
        await s.create_project("Bracket study")

    run(prepare())

    con = sqlite3.connect(db)
    try:
        con.execute("UPDATE projects SET owner = NULL")
        con.execute("UPDATE kb_entries SET owner = NULL")
        con.commit()
    finally:
        con.close()

    async def reopen():
        s = _store(tmp_path)
        await s.init()
        return await s.list_projects()

    projects = run(reopen())
    assert [p.owner for p in projects] == [LOCAL.key]


def test_a_catalogue_item_is_always_the_builds(tmp_path):
    """The invariant: catalog_item_id set means owner is the build.

    It is load-bearing rather than tidy. The loader decides whether an item is
    already loaded by asking for the build's copy, so a catalogue row filed
    under a person is a row the loader cannot see — and it imports the item a
    second time beside it, which is how this was found.
    """
    _legacy_db(tmp_path / "db.sqlite")
    db = tmp_path / "db.sqlite"

    async def go():
        s = _store(tmp_path)
        await s.init()
        # The legacy back-fill's path: an ordinary-looking row is recognised as
        # a catalogue item after the fact.
        ordinary = next(p for p in await s.list_projects() if p.name == "Bracket study")
        assert ordinary.owner == LOCAL.key
        await s.set_catalog_item_id(ordinary.id, "house/late")
        return await s.list_projects()

    projects = run(go())
    con = sqlite3.connect(db)
    try:
        rows = con.execute(
            "SELECT owner FROM projects WHERE catalog_item_id IS NOT NULL"
        ).fetchall()
    finally:
        con.close()
    assert rows, "the fixture should leave at least one catalogue item"
    assert {r[0] for r in rows} == {SYSTEM_KEY}
    assert all(p.owner == SYSTEM_KEY for p in projects if p.catalog_item_id is not None)


def test_clearing_the_catalogue_mark_leaves_the_owner_alone(tmp_path):
    # Un-marking is not un-sharing: the row stops being a catalogue item, and
    # who it belongs to is a separate question this does not answer.
    async def go():
        s = _store(tmp_path)
        await s.init()
        p = await s.create_project("Demo", catalog_item_id="house/demo", owner=SYSTEM_KEY)
        await s.set_catalog_item_id(p.id, None)
        return await s.get_project(p.id)

    after = run(go())
    assert after.catalog_item_id is None
    assert after.owner == SYSTEM_KEY


def test_migrating_twice_changes_nothing(tmp_path):
    _legacy_db(tmp_path / "db.sqlite")

    async def go():
        s = _store(tmp_path)
        await s.init()
        first = {p.name: p.owner for p in await s.list_projects()}
        await s.init()
        return first, {p.name: p.owner for p in await s.list_projects()}

    first, second = run(go())
    assert first == second
