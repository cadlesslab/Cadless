"""Persistence layer: SQLite metadata + filesystem artifact blobs.

Pure storage — no FastAPI/web imports. Projects own script versions; versions own
artifacts (STEP/GLB) whose bytes live on disk under ``artifacts_dir/<version_id>/``.
The parametric script is the source of truth, so every generation/edit is a
re-runnable version.

Contract (consumed by the API issues):
  Projects:  create_project, list_projects, get_project, rename_project, delete_project
  Versions:  add_version, list_versions, get_version, set_current_version
  Artifacts: add_artifact, list_artifacts, get_artifact (+ version_artifact_dir)
  Chat:      get_or_create_session, add_message, update_message, list_messages
             (messages carry an optional neutral ContentBlock list via blocks_json)
"""

from __future__ import annotations

import json
import logging
import math
import shutil
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite

from cadless.config import Settings, settings
from cadless.identity import (
    LOCAL,
    SYSTEM_KEY,
    UNSCOPED,
    Owner,
    acting_owner,
    visible_owners,
    writable_owners,
)
from cadless.llm.types import ContentBlock

logger = logging.getLogger(__name__)

# Host parameters per statement stay well under the lowest SQLite build cap (999).
_SQL_IN_CHUNK = 500

# Publishing a project somewhere used to be this engine's own business, and its
# two columns sat on `projects`. The capability belongs to whichever build
# implements it, and this is the key the old values are carried forward under so
# that build can find them — the capability's name, not any particular
# implementation's. Named here because a migration is about the past by
# definition; nothing outside `_carry_publish_forward` uses it.
LEGACY_PUBLISH_PLUGIN = "publish"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    current_version_id INTEGER,
    branched_from_version_id INTEGER,
    derived_from_project_id INTEGER,
    catalog_item_id TEXT,
    owner TEXT
);
CREATE TABLE IF NOT EXISTS project_plugin_data (
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    plugin TEXT NOT NULL,
    data_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (project_id, plugin)
);
CREATE TABLE IF NOT EXISTS script_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    prompt TEXT NOT NULL,
    code TEXT,
    ok INTEGER NOT NULL,
    error TEXT,
    volume REAL,
    bbox_json TEXT,
    parameters_json TEXT,
    parent_version_id INTEGER,
    candidate_of_version_id INTEGER,
    plan_step INTEGER,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version_id INTEGER NOT NULL REFERENCES script_versions(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    path TEXT NOT NULL,
    bytes INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL UNIQUE REFERENCES projects(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT,
    blocks_json TEXT,
    status TEXT NOT NULL DEFAULT 'ok',
    error TEXT,
    version_id INTEGER REFERENCES script_versions(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_messages_session_seq
    ON chat_messages(session_id, seq);
CREATE TABLE IF NOT EXISTS kb_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nl_intent TEXT NOT NULL,
    code TEXT NOT NULL,
    params_json TEXT,
    signature_json TEXT,
    provenance_json TEXT,
    embedding_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    owner TEXT
);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two equal-length vectors, in ``[-1.0, 1.0]``.

    Pure (no I/O) so the brute-force KB ranking math is unit-testable on its own.
    A zero-magnitude vector yields ``0.0`` rather than dividing by zero.
    """
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _owner_sql(
    owner: Owner, column: str = "projects.owner", *, write: bool = False
) -> tuple[str, tuple[str, ...]]:
    """A SQL predicate restricting ``column`` to what ``owner`` may reach.

    ``write=True`` asks the narrower question. Reading the build's rows is why a
    bundled catalogue item appears for everybody; writing them is a different
    act, and one predicate answering both would let any principal rename or
    delete what every other principal reads.

    Returns ``"1"`` for an engine-internal caller, so one query text serves both
    cases and no call site has to branch on whether it is scoped. The rules
    themselves live in :func:`cadless.identity.visible_owners` and
    :func:`cadless.identity.writable_owners` and are only *rendered* here — a
    query that spelled one out for itself would be a second copy to keep in
    step, and the failure mode of a copy drifting is one principal reaching
    another's rows.

    ``column`` is never caller-supplied. It is a literal at each call site
    naming which table's owner column this predicate is being pasted beside,
    which is what lets a query reach ownership through a join.
    """
    allowed = writable_owners(owner) if write else visible_owners(owner)
    if allowed is None:
        return "1", ()
    placeholders = ",".join("?" * len(allowed))
    return f"{column} IN ({placeholders})", allowed


@dataclass
class Project:
    id: int
    name: str
    created_at: str
    updated_at: str
    current_version_id: int | None
    branched_from_version_id: int | None = None
    # Customize-from-catalog provenance (#22): set on deep clones to the source
    # project's id, so the UI can show "based on <name>" and link back to it.
    derived_from_project_id: int | None = None
    # The catalog item this project was loaded from, and the whole reason the
    # project is read-only. It lives on the row rather than in the sidecar
    # ledger because it is written by the same transaction that creates the
    # project and goes away with it: a file beside the db could be half-written
    # or read as absent, and for as long as it was every catalog item would look
    # like an ordinary editable project. NULL for anything a user made,
    # clones included — which is what makes a clone editable.
    catalog_item_id: str | None = None
    # Who this project belongs to. ``LOCAL.key`` on a build nobody is hosting,
    # a build-supplied principal key where one is, and ``SYSTEM_KEY`` for the
    # bundled catalogue, which every principal reads and none of them edits.
    # Never NULL on a row this engine wrote: a NULL owner is visible to nobody,
    # which is the right way round for a bug but is still a bug — the migration
    # fills any it finds.
    owner: str = LOCAL.key


@dataclass
class ScriptVersion:
    id: int
    project_id: int
    prompt: str
    code: str | None
    ok: bool
    error: str | None
    volume: float | None
    bbox: tuple[float, float, float] | None
    created_at: str
    parameters: dict = field(default_factory=dict)
    parent_version_id: int | None = None
    candidate_of_version_id: int | None = None
    plan_step: int | None = None


@dataclass
class Artifact:
    id: int
    version_id: int
    kind: str
    path: str
    bytes: int


@dataclass
class ChatSession:
    id: int
    project_id: int
    created_at: str
    updated_at: str


@dataclass
class ChatMessage:
    id: int
    session_id: int
    seq: int
    role: str
    content: str | None
    status: str
    error: str | None
    version_id: int | None
    created_at: str
    blocks: list[ContentBlock] = field(default_factory=list)


@dataclass
class KBEntry:
    """A reusable knowledge-base example.

    Owner-scoped and cross-project: rows are shared across all of one owner's
    projects, and across none of anyone else's. The originating project/version
    live inside ``provenance``, not as a foreign key, so an entry stays
    retrievable after its source project is deleted — which is also why the
    owner is a column here rather than something a join could recover: the
    project it came from may be gone, and the entry is still somebody's.
    """

    id: int
    nl_intent: str
    code: str
    embedding: list[float]
    created_at: str
    params: dict = field(default_factory=dict)
    geometry_signature: dict = field(default_factory=dict)
    provenance: dict = field(default_factory=dict)
    owner: str = LOCAL.key


def _blocks_to_json(blocks: list[ContentBlock] | None) -> str | None:
    """Serialize neutral content blocks to a JSON array, or None when empty."""
    if not blocks:
        return None
    return json.dumps([b.model_dump() for b in blocks])


def _blocks_from_json(raw: str | None) -> list[ContentBlock]:
    """Parse a stored ``blocks_json`` payload back into content blocks."""
    if not raw:
        return []
    return [ContentBlock(**b) for b in json.loads(raw)]


class Store:
    def __init__(
        self,
        db_path: Path | None = None,
        artifacts_dir: Path | None = None,
        config: Settings | None = None,
    ):
        cfg = config or settings
        self.db_path = Path(db_path or cfg.db_path)
        self.artifacts_dir = Path(artifacts_dir or cfg.artifacts_dir)

    async def init(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        async with self._connect() as db:
            await db.executescript(_SCHEMA)
            await self._migrate(db)
            await db.commit()

    @staticmethod
    async def _migrate(db) -> None:
        """Additive column migrations for databases created before a column existed.

        ``CREATE TABLE IF NOT EXISTS`` never alters an existing table, so columns
        added after first deploy must be back-filled here (idempotent).
        """
        proj_rows = await (await db.execute("PRAGMA table_info(projects)")).fetchall()
        proj_cols = {r["name"] for r in proj_rows}
        if proj_cols and "branched_from_version_id" not in proj_cols:
            await db.execute("ALTER TABLE projects ADD COLUMN branched_from_version_id INTEGER")
        if proj_cols and "derived_from_project_id" not in proj_cols:
            # Customize-from-catalog provenance (#22): the project this one was
            # deep-cloned from (usually a catalog item). NULL for non-clones.
            await db.execute("ALTER TABLE projects ADD COLUMN derived_from_project_id INTEGER")
        if proj_cols and "catalog_item_id" not in proj_cols:
            # Which catalog item this project is. Legacy rows back-fill
            # to NULL here and are filled in from the sidecar ledger at startup —
            # see ``cadless.catalog.loader.backfill_catalog_item_ids``. Until that
            # runs they read as ordinary projects, so the back-fill happens before
            # the first request rather than lazily.
            await db.execute("ALTER TABLE projects ADD COLUMN catalog_item_id TEXT")
        if proj_cols and "owner" not in proj_cols:
            # Who each project belongs to. A database written before this column
            # existed was a single user's by definition, so its rows are theirs
            # — except the catalogue, which came with the build rather than from
            # them. That split is what keeps a bundled item readable after a
            # build starts hosting more than one person.
            await db.execute("ALTER TABLE projects ADD COLUMN owner TEXT")
        # Run on every init() rather than only beside the ALTER, and filtered to
        # NULL so it never re-owns a row. An owner arriving as NULL some other
        # way — a column added by hand, a row written by an older build against
        # a newer file — would otherwise be visible to nobody at all, since the
        # visibility rule matches on equality. Fail-closed is the right way
        # round for a bug, but it is still a bug, and this is where it is fixed
        # instead of being reported as missing projects.
        await db.execute(
            "UPDATE projects SET owner = CASE WHEN catalog_item_id IS NULL THEN ? ELSE ? END "
            "WHERE owner IS NULL",
            (LOCAL.key, SYSTEM_KEY),
        )
        kb_rows = await (await db.execute("PRAGMA table_info(kb_entries)")).fetchall()
        kb_cols = {r["name"] for r in kb_rows}
        if kb_cols and "owner" not in kb_cols:
            # The knowledge base has described itself as account-scoped since it
            # was written, while having no column to scope on. Distilled code is
            # fed back as grounding, so an unscoped table hands one principal's
            # work to the next one to ask a similar question. Legacy rows belong
            # to the single user whose work produced them.
            await db.execute("ALTER TABLE kb_entries ADD COLUMN owner TEXT")
        await db.execute("UPDATE kb_entries SET owner = ? WHERE owner IS NULL", (LOCAL.key,))
        # Publishing used to be this engine's own business, and its two columns
        # sat on `projects`. It belongs to whichever build implements it, so the
        # values move into the per-plugin table and nothing here reads the
        # columns again.
        #
        # Moved, not dropped. This store has only ever added columns, and a drop
        # would rewrite the table under every older deployment to reclaim two
        # nulls — so an old database keeps two columns nobody reads, which costs
        # nothing and cannot fail. A database made after this change never has
        # them at all.
        #
        # `INSERT OR IGNORE` is what makes the move idempotent: the columns stay
        # readable, so this runs again on every `init()`, and a plugin that has
        # since written its own record must not have it replaced by the older
        # one underneath.
        if {"published_slug", "publish_meta_json"} <= proj_cols:
            await _carry_publish_forward(db)
        # One project per catalog item *per owner*, enforced rather than assumed:
        # importing the same item twice is the failure this column exists to
        # prevent, so the db refuses it outright instead of leaving a duplicate
        # for a later query to pick between. Partial, so the NULLs on every
        # ordinary project don't collide. Created after the ALTERs above, which
        # is why it is not in _SCHEMA.
        #
        # It was table-wide until the owner column arrived, and had to stop
        # being: two people importing the same item is not a duplicate, it is
        # two people. The old index is dropped by name rather than left in
        # place, because leaving it would keep enforcing exactly the rule this
        # replaces — the second importer would be refused, and the refusal would
        # look like the item already being held.
        #
        # A downgrade is not a path this supports, the same as the publish
        # carry-forward above: an older build reopening this database recreates
        # the table-wide index, which fails once two owners hold one item, and
        # `init` raises rather than starting.
        await db.execute("DROP INDEX IF EXISTS idx_projects_catalog_item_id")
        await db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_projects_owner_catalog_item_id "
            "ON projects(owner, catalog_item_id) WHERE catalog_item_id IS NOT NULL"
        )
        rows = await (await db.execute("PRAGMA table_info(script_versions)")).fetchall()
        cols = {r["name"] for r in rows}
        if "parameters_json" not in cols:
            await db.execute("ALTER TABLE script_versions ADD COLUMN parameters_json TEXT")
        if "parent_version_id" not in cols:
            await db.execute("ALTER TABLE script_versions ADD COLUMN parent_version_id INTEGER")
        if "candidate_of_version_id" not in cols:
            # Checkpoint racing: non-NULL marks a losing candidate row and
            # points at the winning sibling version it lost to. NULL = a normal/winner
            # version. Additive; legacy rows back-fill to NULL.
            await db.execute(
                "ALTER TABLE script_versions ADD COLUMN candidate_of_version_id INTEGER"
            )
        if "plan_step" not in cols:
            # UI narration: an OPTIONAL pointer to the active plan step
            # (1-based index) at the moment this version's checkpoint was written, so
            # the UI can narrate "rolled back to step N". NULL = no active plan; a
            # lightweight annotation over the existing chain, no new snapshot store.
            await db.execute("ALTER TABLE script_versions ADD COLUMN plan_step INTEGER")
        msg_rows = await (await db.execute("PRAGMA table_info(chat_messages)")).fetchall()
        msg_cols = {r["name"] for r in msg_rows}
        if msg_cols and "blocks_json" not in msg_cols:
            # Legacy chat_messages rows back-fill to NULL (an empty block list).
            await db.execute("ALTER TABLE chat_messages ADD COLUMN blocks_json TEXT")
        # Seed a chat session for every project that lacks one (1:1 invariant). Idempotent:
        # the LEFT JOIN filter skips projects that already have a session, so a second
        # init() is a no-op. No message rows are synthesized for legacy versions here.
        ts = _now()
        await db.execute(
            "INSERT INTO chat_sessions(project_id, created_at, updated_at) "
            "SELECT p.id, ?, ? FROM projects p "
            "LEFT JOIN chat_sessions s ON s.project_id = p.id "
            "WHERE s.id IS NULL",
            (ts, ts),
        )

    def _connect(self) -> aiosqlite.Connection:
        conn = aiosqlite.connect(self.db_path)
        return _ConfiguredConnection(conn)

    def version_artifact_dir(self, version_id: int) -> str:
        d = self.artifacts_dir / str(version_id)
        d.mkdir(parents=True, exist_ok=True)
        return str(d)

    # ---- projects -------------------------------------------------------
    async def create_project(
        self, name: str, *, catalog_item_id: str | None = None, owner: Owner = UNSCOPED
    ) -> Project:
        """Create a project. ``catalog_item_id`` marks it as that catalog item.

        The catalog loader passes it so a loaded item is read-only from the
        moment its row exists — there is no window where it is on disk as an
        ordinary project. Everything else leaves it NULL and stays editable.

        ``owner`` is written in the same transaction for the same reason: there
        must be no moment at which a row exists belonging to nobody, because a
        row belonging to nobody is a row the visibility rule hides from
        everybody. It defaults to the single user of an unhosted build, which is
        what every caller wants until a build starts hosting more than one
        person; ``SYSTEM_KEY`` is what the bundled catalogue is loaded under.
        """
        ts = _now()
        mine = acting_owner(owner)
        async with self._connect() as db:
            cur = await db.execute(
                "INSERT INTO projects(name, created_at, updated_at, catalog_item_id, owner) "
                "VALUES (?,?,?,?,?)",
                (name, ts, ts, catalog_item_id, mine),
            )
            project_id = cur.lastrowid
            await db.execute(
                "INSERT INTO chat_sessions(project_id, created_at, updated_at) VALUES (?,?,?)",
                (project_id, ts, ts),
            )
            await db.commit()
            return Project(
                project_id, name, ts, ts, None, catalog_item_id=catalog_item_id, owner=mine
            )

    async def project_id_for_catalog_item(
        self, item_id: str, *, owner: Owner = UNSCOPED
    ) -> int | None:
        """The project this catalog item was loaded as, or ``None`` if it is not.

        This is what makes loading idempotent. Asking the db rather than the
        ledger means a ledger nobody can read cannot make an already-loaded item
        look absent — which would import the whole catalog a second time and
        leave the first copy behind as an editable orphan.

        Scoped, and it has to be: unscoped, the second person to import an item
        would be told it was already held and handed the first person's project.
        """
        pred, params = _owner_sql(owner)
        async with self._connect() as db:
            row = await (
                await db.execute(
                    f"SELECT id FROM projects WHERE catalog_item_id=? AND {pred} "
                    "ORDER BY id LIMIT 1",
                    (item_id, *params),
                )
            ).fetchone()
        return row["id"] if row else None

    async def catalog_item_ids(self, *, owner: Owner = UNSCOPED) -> dict[int, str]:
        """``project_id -> catalog item id`` for every catalog project.

        One query for a whole response: the project list needs to mark each row
        and would otherwise ask per project.
        """
        pred, params = _owner_sql(owner)
        async with self._connect() as db:
            rows = await (
                await db.execute(
                    "SELECT id, catalog_item_id FROM projects "
                    f"WHERE catalog_item_id IS NOT NULL AND {pred}",
                    params,
                )
            ).fetchall()
        return {r["id"]: r["catalog_item_id"] for r in rows}

    async def set_catalog_item_id(self, project_id: int, item_id: str | None) -> None:
        """Point an existing project row at a catalog item (or clear it).

        Only the legacy ledger back-fill needs this; loading sets the column at
        insert. Marking a row as a catalogue item also files it under the build,
        because that is what a catalogue item is — shared, read-only, nobody's in
        particular. Leaving the owner alone would let a legacy database end up
        holding catalogue items belonging to one person, which the loader would
        not recognise as loaded and would import a second time beside them.

        Unscoped, and deliberately absent from the request-facing view: it is a
        migration helper, and a route that could call it could hand its own
        project to every principal by declaring it a catalogue item.
        """
        async with self._connect() as db:
            if item_id is None:
                await db.execute(
                    "UPDATE projects SET catalog_item_id=NULL WHERE id=?", (project_id,)
                )
            else:
                await db.execute(
                    "UPDATE projects SET catalog_item_id=?, owner=? WHERE id=?",
                    (item_id, SYSTEM_KEY, project_id),
                )
            await db.commit()

    async def list_projects(self, *, owner: Owner = UNSCOPED) -> list[Project]:
        pred, params = _owner_sql(owner)
        async with self._connect() as db:
            rows = await (
                await db.execute(f"SELECT * FROM projects WHERE {pred} ORDER BY id", params)
            ).fetchall()
        return [_project(r) for r in rows]

    async def get_project(self, project_id: int, *, owner: Owner = UNSCOPED) -> Project | None:
        pred, params = _owner_sql(owner)
        async with self._connect() as db:
            row = await (
                await db.execute(
                    f"SELECT * FROM projects WHERE id=? AND {pred}", (project_id, *params)
                )
            ).fetchone()
        return _project(row) if row else None

    async def rename_project(
        self, project_id: int, name: str, *, owner: Owner = UNSCOPED
    ) -> Project | None:
        """Rename a project, or ``None`` when this owner has no such project.

        Somebody else's project answers ``None`` rather than a refusal, which is
        the same answer a project that does not exist gives. That is deliberate:
        telling the two apart would confirm the row is there.
        """
        pred, params = _owner_sql(owner, write=True)
        async with self._connect() as db:
            cur = await db.execute(
                f"UPDATE projects SET name=?, updated_at=? WHERE id=? AND {pred}",
                (name, _now(), project_id, *params),
            )
            await db.commit()
            if cur.rowcount == 0:
                return None
        return await self.get_project(project_id, owner=owner)

    async def record_plugin_data(
        self, project_id: int, plugin: str, data: Mapping[str, Any], *, owner: Owner = UNSCOPED
    ) -> bool:
        """Keep one build's own record against a project, or say there is no such project.

        The place a build installed beside this one puts what it needs to
        remember about a project — where it published it, what was chosen at the
        time — without the schema naming that build or this engine reading what
        it wrote. Keyed by the build, so two of them cannot overwrite each
        other's half of a shared blob.

        Replaces rather than merges, on the same rule the publish record it
        replaced followed: the record describes the last thing that happened,
        and a value carried over from an earlier one it was not part of would be
        a claim nobody made this time. The caller assembles the whole record.

        The foreign key is what removes it: deleting a project takes its plugin
        records with it, so a build that is uninstalled and reinstalled does not
        find rows pointing at projects that are gone.

        One statement, and that is the point rather than brevity. Asking whether
        the project exists and then writing would be two, with nothing holding
        the answer still between them — a project deleted in the gap turns a
        foreign key into an exception, and the caller most likely to be here is
        recording a publish that has *already happened*. Reporting that as a
        failure sends the next attempt into a duplicate. `WHERE EXISTS` makes
        the check and the write the same operation, so the honest answers are
        "written" and "there is no such project" and there is no third one.
        """
        pred, owner_params = _owner_sql(owner, write=True)
        async with self._connect() as db:
            cur = await db.execute(
                "INSERT INTO project_plugin_data(project_id, plugin, data_json, updated_at)"
                f" SELECT ?,?,?,? WHERE EXISTS (SELECT 1 FROM projects WHERE id=? AND {pred})"
                " ON CONFLICT(project_id, plugin) DO UPDATE SET"
                " data_json=excluded.data_json, updated_at=excluded.updated_at",
                (project_id, plugin, json.dumps(dict(data)), _now(), project_id, *owner_params),
            )
            await db.commit()
        return cur.rowcount > 0

    async def plugin_data_for(
        self, project_ids: Sequence[int], plugin: str, *, owner: Owner = UNSCOPED
    ) -> dict[int, dict]:
        """The same, for many projects at once, keyed by project id.

        A listing asks this about every project it shows, and each `plugin_data`
        opens its own connection — so the per-row version turns one page into as
        many connections as the user has projects. Absent records are absent
        from the answer rather than present as ``None``, so a caller still tells
        "never written" from "written empty" by asking whether the key is there.

        The ids arrive from the caller, so ownership is decided here by joining
        rather than by trusting the list: a caller that assembled it from its own
        listing passes only its own, and one that did not gets nothing back for
        the rest.
        """
        found: dict[int, dict] = {}
        ids = list(project_ids)
        if not ids:
            return found
        pred, owner_params = _owner_sql(owner)
        async with self._connect() as db:
            for start in range(0, len(ids), _SQL_IN_CHUNK):
                chunk = ids[start : start + _SQL_IN_CHUNK]
                placeholders = ",".join("?" * len(chunk))
                rows = await (
                    await db.execute(
                        "SELECT d.project_id, d.data_json FROM project_plugin_data d"
                        " JOIN projects ON projects.id = d.project_id"
                        f" WHERE d.plugin=? AND d.project_id IN ({placeholders}) AND {pred}",
                        (plugin, *chunk, *owner_params),
                    )
                ).fetchall()
                for row in rows:
                    record = _plugin_record(row["data_json"])
                    if record is not None:
                        found[row["project_id"]] = record
        return found

    async def plugin_data(
        self, project_id: int, plugin: str, *, owner: Owner = UNSCOPED
    ) -> dict | None:
        """What that build last recorded against this project, or nothing.

        Nothing rather than an empty record: "never written" and "written empty"
        are different answers, and a build deciding whether this is the first
        publish needs to tell them apart. Somebody else's project is a third
        case that answers the same as the first, on purpose.
        """
        pred, owner_params = _owner_sql(owner)
        async with self._connect() as db:
            row = await (
                await db.execute(
                    "SELECT d.data_json FROM project_plugin_data d"
                    " JOIN projects ON projects.id = d.project_id"
                    f" WHERE d.project_id=? AND d.plugin=? AND {pred}",
                    (project_id, plugin, *owner_params),
                )
            ).fetchone()
        if row is None:
            return None
        return _plugin_record(row["data_json"])

    async def delete_project(self, project_id: int, *, owner: Owner = UNSCOPED) -> bool:
        """Delete a project and its artifact blobs; ``False`` if this owner has none.

        The version ids are collected under the same scope as the delete, so a
        refused delete cannot take anybody's files with it — the list is empty
        for a project this owner cannot see, and the ``DELETE`` matches nothing.
        """
        pred, owner_params = _owner_sql(owner, write=True)
        version_ids = [v.id for v in await self.list_versions(project_id, owner=owner)]
        async with self._connect() as db:
            cur = await db.execute(
                f"DELETE FROM projects WHERE id=? AND {pred}", (project_id, *owner_params)
            )
            await db.commit()
            deleted = cur.rowcount > 0
        if not deleted:
            return False
        for vid in version_ids:  # remove blob dirs too
            shutil.rmtree(self.artifacts_dir / str(vid), ignore_errors=True)
        return True

    async def branch_project(
        self,
        version_id: int,
        *,
        name: str | None = None,
        owner: Owner = UNSCOPED,
    ) -> Project | None:
        """Fork ``version_id`` into a brand-new project (an alternative line).

        Reuses ``create_project`` + ``add_version`` + ``set_current_version``: the
        new project is seeded with a single version copying the source version's
        code/params (its starting and current model), and ``branched_from_version_id``
        records the origin. The source project is left untouched. Returns ``None``
        when the source version does not exist.

        The branch belongs to whoever asked for it, not to whoever owns the
        source. Branching the bundled catalogue is exactly the case: the source
        is the build's and read-only, and the point of the operation is to come
        away with something of your own that you can change.
        """
        source = await self.get_version(version_id, owner=owner)
        if source is None:
            return None
        mine = acting_owner(owner)
        origin = await self.get_project(source.project_id, owner=owner)
        branch_name = name or (f"{origin.name} (branch)" if origin else "Branch")
        project = await self.create_project(branch_name, owner=mine)
        seed = await self.add_version(
            project.id,
            source.prompt,
            source.code,
            source.ok,
            source.error,
            source.volume,
            source.bbox,
            parameters=dict(source.parameters),
            owner=mine,
        )
        await self.set_current_version(project.id, seed.id, owner=mine)
        async with self._connect() as db:
            await db.execute(
                "UPDATE projects SET branched_from_version_id=?, updated_at=? WHERE id=?",
                (version_id, _now(), project.id),
            )
            await db.commit()
        return await self.get_project(project.id, owner=mine)

    async def clone_project(
        self,
        project_id: int,
        *,
        name: str | None = None,
        owner: Owner = UNSCOPED,
    ) -> Project | None:
        """Deep-copy a whole project into a new one: every main-line version
        (prompt + code + geometry + artifact files), the chat history, and the
        current-version pointer. Unlike :meth:`branch_project` (which seeds a
        single version), this reproduces the full conversation and code ladder so
        the copy opens fully populated and editable. Forge candidate rows (losers)
        are not copied. Returns ``None`` if the source project is missing.

        Customize-from-catalog (#22) rides on exactly this deep copy:

        * **Context seeding is replayed history, not system-prompt injection.**
          The catalog loader already persisted each step's re-authored transcript
          (user_prompt / assistant_message) as ordinary chat messages, and this
          method copies them verbatim. ``POST /chat`` then assembles the clone's
          first-turn model context from that replayed history plus a leading
          current-model block (code + params) — see
          ``backend.routers.chat._replay_history`` / ``_current_model`` and
          ``cadless.agent._model_context_block``. So the first message on a
          clone can immediately be a modification ("make the bore 10 mm") with
          the whole baseline build conversation in context. No LLM/Bedrock call
          happens here: cloning is pure store + file copying.
        * **Provenance**: ``derived_from_project_id`` records the source project
          so the API/UI can resolve "based on <catalog item>" and link back.

        The copy belongs to whoever asked for it, which is what makes
        customize-from-catalog work at all: the source is the build's read-only
        catalogue row and the copy has to be the person's own and editable.
        Everything read out of the source is read under the caller's scope, so a
        project they cannot see is one they cannot copy either.
        """
        source = await self.get_project(project_id, owner=owner)
        if source is None:
            return None
        mine = acting_owner(owner)
        clone = await self.create_project(name or f"{source.name} (copy)", owner=mine)
        async with self._connect() as db:
            await db.execute(
                "UPDATE projects SET derived_from_project_id=? WHERE id=?", (project_id, clone.id)
            )
            await db.commit()
        id_map: dict[int, int] = {}
        for v in await self.list_versions(project_id, owner=owner):
            if v.candidate_of_version_id is not None:
                continue  # skip forge losers; copy only the main line
            new_v = await self.add_version(
                clone.id,
                v.prompt,
                v.code,
                v.ok,
                v.error,
                v.volume,
                v.bbox,
                parameters=dict(v.parameters),
                parent_version_id=id_map.get(v.parent_version_id),
                plan_step=v.plan_step,
                owner=mine,
            )
            id_map[v.id] = new_v.id
            for a in await self.list_artifacts(v.id, owner=owner):
                src = Path(a.path)
                if src.exists():
                    dst = Path(self.version_artifact_dir(new_v.id)) / src.name
                    shutil.copyfile(src, dst)
                    await self.add_artifact(new_v.id, a.kind, str(dst), owner=mine)
        src_session = await self.get_or_create_session(project_id, owner=owner)
        new_session = await self.get_or_create_session(clone.id, owner=mine)
        for m in await self.list_messages(src_session.id, owner=owner):
            await self.add_message(
                new_session.id,
                m.role,
                m.content,
                status=m.status,
                error=m.error,
                version_id=id_map.get(m.version_id) if m.version_id else None,
                blocks=m.blocks,
                owner=mine,
            )
        if source.current_version_id is not None and source.current_version_id in id_map:
            await self.set_current_version(clone.id, id_map[source.current_version_id], owner=mine)
        return await self.get_project(clone.id, owner=mine)

    # ---- versions -------------------------------------------------------
    async def add_version(
        self,
        project_id: int,
        prompt: str,
        code: str | None,
        ok: bool,
        error: str | None = None,
        volume: float | None = None,
        bbox: tuple[float, float, float] | None = None,
        parameters: dict | None = None,
        parent_version_id: int | None = None,
        candidate_of_version_id: int | None = None,
        plan_step: int | None = None,
        *,
        owner: Owner = UNSCOPED,
    ) -> ScriptVersion:
        """Append a version to a project.

        Raises :class:`LookupError` when this owner has no such project, rather
        than writing into somebody else's. It is a raise and not a ``None``
        because every caller here has already found the project — a miss means
        the project went away underneath, or that a caller reached past its own
        scope, and both are worth stopping for rather than absorbing.
        """
        ts = _now()
        bbox_json = json.dumps(list(bbox)) if bbox else None
        params = parameters or {}
        params_json = json.dumps(params) if params else None
        pred, owner_params = _owner_sql(owner, write=True)
        async with self._connect() as db:
            cur = await db.execute(
                "INSERT INTO script_versions"
                "(project_id,prompt,code,ok,error,volume,bbox_json,parameters_json,"
                "parent_version_id,candidate_of_version_id,plan_step,created_at)"
                " SELECT ?,?,?,?,?,?,?,?,?,?,?,?"
                f" WHERE EXISTS (SELECT 1 FROM projects WHERE id=? AND {pred})",
                (
                    project_id,
                    prompt,
                    code,
                    int(ok),
                    error,
                    volume,
                    bbox_json,
                    params_json,
                    parent_version_id,
                    candidate_of_version_id,
                    plan_step,
                    ts,
                    project_id,
                    *owner_params,
                ),
            )
            if cur.rowcount == 0:
                raise LookupError(f"no project {project_id} for this owner")
            await db.execute("UPDATE projects SET updated_at=? WHERE id=?", (ts, project_id))
            await db.commit()
            return ScriptVersion(
                cur.lastrowid,
                project_id,
                prompt,
                code,
                ok,
                error,
                volume,
                bbox,
                ts,
                params,
                parent_version_id,
                candidate_of_version_id,
                plan_step,
            )

    async def list_versions(
        self, project_id: int, *, owner: Owner = UNSCOPED
    ) -> list[ScriptVersion]:
        pred, params = _owner_sql(owner)
        async with self._connect() as db:
            rows = await (
                await db.execute(
                    "SELECT script_versions.* FROM script_versions"
                    " JOIN projects ON projects.id = script_versions.project_id"
                    f" WHERE script_versions.project_id=? AND {pred} ORDER BY script_versions.id",
                    (project_id, *params),
                )
            ).fetchall()
        return [_version(r) for r in rows]

    async def get_version(
        self, version_id: int, *, owner: Owner = UNSCOPED
    ) -> ScriptVersion | None:
        """One version by id, or ``None`` when this owner cannot see it.

        A version is addressed by its own id with no project in the URL, so the
        project it belongs to — and therefore who owns it — is reached by the
        join rather than supplied by the caller. Guessing an integer is the
        cheapest attack there is against a bare-id route, and this is where it
        stops.
        """
        pred, params = _owner_sql(owner)
        async with self._connect() as db:
            row = await (
                await db.execute(
                    "SELECT script_versions.* FROM script_versions"
                    " JOIN projects ON projects.id = script_versions.project_id"
                    f" WHERE script_versions.id=? AND {pred}",
                    (version_id, *params),
                )
            ).fetchone()
        return _version(row) if row else None

    async def list_candidate_versions(
        self, winner_version_id: int, *, owner: Owner = UNSCOPED
    ) -> list[ScriptVersion]:
        """The losing candidate rows that lost the race to ``winner_version_id``.

        Checkpoint racing: losers are persisted as non-current versions
        flagged with ``candidate_of_version_id`` pointing at the winning sibling, so
        the surviving line's discarded alternatives stay retrievable (e.g. for a UI
        to show the race) without ever being the current version.
        """
        pred, params = _owner_sql(owner)
        async with self._connect() as db:
            rows = await (
                await db.execute(
                    "SELECT script_versions.* FROM script_versions"
                    " JOIN projects ON projects.id = script_versions.project_id"
                    f" WHERE script_versions.candidate_of_version_id=? AND {pred}"
                    " ORDER BY script_versions.id",
                    (winner_version_id, *params),
                )
            ).fetchall()
        return [_version(r) for r in rows]

    async def last_ok_version(
        self, project_id: int, *, owner: Owner = UNSCOPED
    ) -> ScriptVersion | None:
        """The most recent OK (successfully-built) version of a project, or None.

        Pillar 4 safety net: the "last good model" the project should
        fall back to. Losing forge candidate rows are excluded — a fallback target
        must be a real current-eligible version, never a discarded race loser.
        """
        pred, params = _owner_sql(owner)
        async with self._connect() as db:
            row = await (
                await db.execute(
                    "SELECT script_versions.* FROM script_versions"
                    " JOIN projects ON projects.id = script_versions.project_id"
                    f" WHERE script_versions.project_id=? AND script_versions.ok=1"
                    f" AND script_versions.candidate_of_version_id IS NULL AND {pred}"
                    " ORDER BY script_versions.id DESC LIMIT 1",
                    (project_id, *params),
                )
            ).fetchone()
        return _version(row) if row else None

    async def set_current_version(
        self, project_id: int, version_id: int, *, owner: Owner = UNSCOPED
    ) -> bool:
        pred, params = _owner_sql(owner, write=True)
        async with self._connect() as db:
            row = await (
                await db.execute(
                    "SELECT script_versions.id FROM script_versions"
                    " JOIN projects ON projects.id = script_versions.project_id"
                    f" WHERE script_versions.id=? AND script_versions.project_id=? AND {pred}",
                    (version_id, project_id, *params),
                )
            ).fetchone()
            if not row:
                return False
            await db.execute(
                "UPDATE projects SET current_version_id=?, updated_at=? WHERE id=?",
                (version_id, _now(), project_id),
            )
            await db.commit()
        return True

    # ---- artifacts ------------------------------------------------------
    async def add_artifact(
        self, version_id: int, kind: str, path: str, *, owner: Owner = UNSCOPED
    ) -> Artifact:
        """Record an artifact file against a version.

        Raises :class:`LookupError` when this owner has no such version, for the
        same reason :meth:`add_version` does.
        """
        size = Path(path).stat().st_size
        pred, params = _owner_sql(owner, write=True)
        async with self._connect() as db:
            cur = await db.execute(
                "INSERT INTO artifacts(version_id,kind,path,bytes)"
                " SELECT ?,?,?,? FROM script_versions"
                " JOIN projects ON projects.id = script_versions.project_id"
                f" WHERE script_versions.id=? AND {pred}",
                (version_id, kind, str(path), size, version_id, *params),
            )
            if cur.rowcount == 0:
                raise LookupError(f"no version {version_id} for this owner")
            await db.commit()
            return Artifact(cur.lastrowid, version_id, kind, str(path), size)

    async def list_artifacts(self, version_id: int, *, owner: Owner = UNSCOPED) -> list[Artifact]:
        pred, params = _owner_sql(owner)
        async with self._connect() as db:
            rows = await (
                await db.execute(
                    "SELECT artifacts.* FROM artifacts"
                    " JOIN script_versions ON script_versions.id = artifacts.version_id"
                    " JOIN projects ON projects.id = script_versions.project_id"
                    f" WHERE artifacts.version_id=? AND {pred} ORDER BY artifacts.id",
                    (version_id, *params),
                )
            ).fetchall()
        return [_artifact(r) for r in rows]

    async def get_artifact(
        self, version_id: int, kind: str, *, owner: Owner = UNSCOPED
    ) -> Artifact | None:
        """One artifact of a version, or ``None`` when this owner cannot see it.

        The download routes hand back file bytes addressed by a bare version id,
        which makes this the narrowest place a guessed integer could turn into
        somebody else's geometry. Ownership is reached by joining twice, because
        an artifact knows its version and a version knows its project.
        """
        pred, params = _owner_sql(owner)
        async with self._connect() as db:
            row = await (
                await db.execute(
                    "SELECT artifacts.* FROM artifacts"
                    " JOIN script_versions ON script_versions.id = artifacts.version_id"
                    " JOIN projects ON projects.id = script_versions.project_id"
                    f" WHERE artifacts.version_id=? AND artifacts.kind=? AND {pred}"
                    " ORDER BY artifacts.id DESC LIMIT 1",
                    (version_id, kind, *params),
                )
            ).fetchone()
        return _artifact(row) if row else None

    async def thumbnail_version_ids(
        self, project_ids: Sequence[int], *, owner: Owner = UNSCOPED
    ) -> dict[int, int]:
        """Map each project id to the version that actually carries its thumbnail.

        A catalog item's thumbnail is baked onto one specific version at load
        time, so this — not ``projects.current_version_id`` — is what a thumbnail
        URL has to name; the two diverge the moment the current version moves.
        Projects without a thumbnail artifact are simply absent from the result.
        """
        ids = list(dict.fromkeys(project_ids))
        if not ids:
            return {}
        found: dict[int, int] = {}
        pred, owner_params = _owner_sql(owner)
        async with self._connect() as db:
            # Chunked: callers size this by the whole catalog ledger, and SQLite's
            # host-parameter cap is only 999 on some builds.
            for start in range(0, len(ids), _SQL_IN_CHUNK):
                chunk = ids[start : start + _SQL_IN_CHUNK]
                placeholders = ",".join("?" * len(chunk))
                rows = await (
                    await db.execute(
                        # Bare columns alongside a lone MAX() come from the
                        # matching row in SQLite, so this picks the newest
                        # thumbnail artifact per project — the same "latest wins"
                        # rule as get_artifact.
                        "SELECT v.project_id AS project_id, a.version_id AS version_id, MAX(a.id) "
                        "FROM artifacts a JOIN script_versions v ON v.id = a.version_id "
                        "JOIN projects ON projects.id = v.project_id "
                        f"WHERE a.kind = 'thumbnail' AND v.project_id IN ({placeholders}) "
                        f"AND {pred} "
                        "GROUP BY v.project_id",
                        (*chunk, *owner_params),
                    )
                ).fetchall()
                found.update({r["project_id"]: r["version_id"] for r in rows})
        return found

    async def all_artifact_paths(self) -> set[str]:
        """Every artifact path on record, for any owner.

        Deliberately unscoped and deliberately not on the scoped view: its only
        caller is :meth:`sweep_orphans`, whose whole job is to compare the set of
        referenced files against what is on disk. Scoped, it would report every
        other owner's files as unreferenced and delete them.
        """
        async with self._connect() as db:
            rows = await (await db.execute("SELECT path FROM artifacts")).fetchall()
        return {r["path"] for r in rows}

    # ---- chat sessions + messages ---------------------------
    async def get_or_create_session(
        self, project_id: int, *, owner: Owner = UNSCOPED
    ) -> ChatSession:
        """Return the project's single chat session, creating it if absent.

        The UNIQUE(project_id) constraint enforces the one-session-per-project
        invariant; this method is the idempotent accessor for it.

        Raises :class:`LookupError` when this owner has no such project. It has
        to: a session id is the key to a whole transcript, and handing one back
        for somebody else's project would be handing over the conversation.

        The two halves ask different questions on purpose. Finding an existing
        session is a read, so it reaches the build's rows — which is what lets
        anybody clone a catalogue item and carry its conversation across.
        Creating one is a write, so it does not: a project you may read but not
        change is not a project you may start a transcript on.
        """
        read_pred, read_params = _owner_sql(owner)
        write_pred, write_params = _owner_sql(owner, write=True)
        async with self._connect() as db:
            row = await (
                await db.execute(
                    "SELECT chat_sessions.* FROM chat_sessions"
                    " JOIN projects ON projects.id = chat_sessions.project_id"
                    f" WHERE chat_sessions.project_id=? AND {read_pred}",
                    (project_id, *read_params),
                )
            ).fetchone()
            if row:
                return _session(row)
            ts = _now()
            cur = await db.execute(
                "INSERT INTO chat_sessions(project_id, created_at, updated_at)"
                f" SELECT ?,?,? WHERE EXISTS (SELECT 1 FROM projects WHERE id=? AND {write_pred})",
                (project_id, ts, ts, project_id, *write_params),
            )
            if cur.rowcount == 0:
                raise LookupError(f"no project {project_id} for this owner")
            await db.commit()
            return ChatSession(cur.lastrowid, project_id, ts, ts)

    async def add_message(
        self,
        session_id: int,
        role: str,
        content: str | None,
        status: str = "ok",
        error: str | None = None,
        version_id: int | None = None,
        blocks: list[ContentBlock] | None = None,
        *,
        owner: Owner = UNSCOPED,
    ) -> ChatMessage:
        """Append a message to a session.

        Raises :class:`LookupError` when this owner has no such session — a
        session id addresses a transcript with no project in sight, so it is
        checked here rather than assumed to have come from a scoped lookup.
        """
        ts = _now()
        blocks = blocks or []
        blocks_json = _blocks_to_json(blocks)
        pred, owner_params = _owner_sql(owner, write=True)
        async with self._connect() as db:
            seq_row = await (
                await db.execute(
                    "SELECT COALESCE(MAX(seq), 0) AS m FROM chat_messages WHERE session_id=?",
                    (session_id,),
                )
            ).fetchone()
            seq = int(seq_row["m"]) + 1
            cur = await db.execute(
                "INSERT INTO chat_messages"
                "(session_id,seq,role,content,blocks_json,status,error,version_id,created_at)"
                " SELECT ?,?,?,?,?,?,?,?,? FROM chat_sessions"
                " JOIN projects ON projects.id = chat_sessions.project_id"
                f" WHERE chat_sessions.id=? AND {pred}",
                (
                    session_id,
                    seq,
                    role,
                    content,
                    blocks_json,
                    status,
                    error,
                    version_id,
                    ts,
                    session_id,
                    *owner_params,
                ),
            )
            if cur.rowcount == 0:
                raise LookupError(f"no chat session {session_id} for this owner")
            await db.execute("UPDATE chat_sessions SET updated_at=? WHERE id=?", (ts, session_id))
            await db.commit()
            return ChatMessage(
                cur.lastrowid, session_id, seq, role, content, status, error, version_id, ts, blocks
            )

    async def update_message(
        self,
        message_id: int,
        *,
        status: str | None = None,
        error: str | None = None,
        version_id: int | None = None,
        content: str | None = None,
        blocks: list[ContentBlock] | None = None,
        owner: Owner = UNSCOPED,
    ) -> ChatMessage | None:
        """Patch the supplied fields of a message; ``None`` args are left untouched.

        A message is two joins away from an owner — message to session to
        project — and is addressed by its own id alone, so both hops are made
        here. Somebody else's message answers ``None``, the same as one that
        does not exist.
        """
        sets, params = [], []
        if status is not None:
            sets.append("status=?")
            params.append(status)
        if error is not None:
            sets.append("error=?")
            params.append(error)
        if version_id is not None:
            sets.append("version_id=?")
            params.append(version_id)
        if content is not None:
            sets.append("content=?")
            params.append(content)
        if blocks is not None:
            sets.append("blocks_json=?")
            params.append(_blocks_to_json(blocks))
        pred, owner_params = _owner_sql(owner)
        write_pred, write_params = _owner_sql(owner, write=True)
        # A correlated EXISTS rather than a join, because SQLite's UPDATE has no
        # FROM clause; the reach to the owner is the same two hops either way.
        writable = (
            "EXISTS (SELECT 1 FROM chat_sessions"
            " JOIN projects ON projects.id = chat_sessions.project_id"
            f" WHERE chat_sessions.id = chat_messages.session_id AND {write_pred})"
        )
        async with self._connect() as db:
            if sets:
                cur = await db.execute(
                    f"UPDATE chat_messages SET {', '.join(sets)} WHERE id=? AND {writable}",
                    (*params, message_id, *write_params),
                )
                await db.commit()
                if cur.rowcount == 0:
                    return None
            row = await (
                await db.execute(
                    "SELECT chat_messages.* FROM chat_messages"
                    " JOIN chat_sessions ON chat_sessions.id = chat_messages.session_id"
                    " JOIN projects ON projects.id = chat_sessions.project_id"
                    f" WHERE chat_messages.id=? AND {pred}",
                    (message_id, *owner_params),
                )
            ).fetchone()
        return _message(row) if row else None

    async def list_messages(self, session_id: int, *, owner: Owner = UNSCOPED) -> list[ChatMessage]:
        pred, params = _owner_sql(owner)
        async with self._connect() as db:
            rows = await (
                await db.execute(
                    "SELECT chat_messages.* FROM chat_messages"
                    " JOIN chat_sessions ON chat_sessions.id = chat_messages.session_id"
                    " JOIN projects ON projects.id = chat_sessions.project_id"
                    f" WHERE chat_messages.session_id=? AND {pred} ORDER BY chat_messages.seq",
                    (session_id, *params),
                )
            ).fetchall()
        return [_message(r) for r in rows]

    # ---- knowledge base + vector index ----------------------
    async def add_kb_entry(
        self,
        nl_intent: str,
        code: str,
        embedding: list[float],
        *,
        params: dict | None = None,
        geometry_signature: dict | None = None,
        provenance: dict | None = None,
        owner: Owner = UNSCOPED,
    ) -> KBEntry:
        """Insert one owner-scoped, cross-project KB entry and return it.

        ``embedding`` is the dense vector (e.g. from ``ChatProvider.embed``) used
        for similarity retrieval; the JSON fields capture the structured params,
        geometry signature (bbox/volume/feature tags) and provenance (project,
        version_id, metrics).

        ``owner`` is whose work this was distilled from. It is written here
        rather than derived from ``provenance`` because provenance is free-form
        and outlives the project it names — the entry has to answer "whose" on
        its own, long after the project it came from was deleted.
        """
        ts = _now()
        params = params or {}
        signature = geometry_signature or {}
        prov = provenance or {}
        mine = acting_owner(owner)
        async with self._connect() as db:
            cur = await db.execute(
                "INSERT INTO kb_entries"
                "(nl_intent,code,params_json,signature_json,provenance_json,"
                "embedding_json,created_at,owner) VALUES (?,?,?,?,?,?,?,?)",
                (
                    nl_intent,
                    code,
                    json.dumps(params) if params else None,
                    json.dumps(signature) if signature else None,
                    json.dumps(prov) if prov else None,
                    json.dumps(embedding),
                    ts,
                    mine,
                ),
            )
            await db.commit()
            return KBEntry(
                cur.lastrowid, nl_intent, code, embedding, ts, params, signature, prov, mine
            )

    async def get_kb_entry(self, entry_id: int, *, owner: Owner = UNSCOPED) -> KBEntry | None:
        pred, params = _owner_sql(owner, "kb_entries.owner")
        async with self._connect() as db:
            row = await (
                await db.execute(
                    f"SELECT * FROM kb_entries WHERE id=? AND {pred}", (entry_id, *params)
                )
            ).fetchone()
        return _kb_entry(row) if row else None

    async def list_kb_entries(self, *, owner: Owner = UNSCOPED) -> list[KBEntry]:
        """One owner's KB entries (cross-project, within that owner), oldest first."""
        pred, params = _owner_sql(owner, "kb_entries.owner")
        async with self._connect() as db:
            rows = await (
                await db.execute(f"SELECT * FROM kb_entries WHERE {pred} ORDER BY id", params)
            ).fetchall()
        return [_kb_entry(r) for r in rows]

    async def query_kb_by_vector(
        self,
        embedding: list[float],
        top_k: int = 5,
        *,
        owner: Owner = UNSCOPED,
    ) -> list[tuple[KBEntry, float]]:
        """Brute-force cosine top-k over this owner's stored vectors (PoC-scale index).

        Returns ``(entry, score)`` pairs sorted by descending similarity. The
        candidate set is cross-project *within one owner* — sufficient at PoC
        scale; swap for a real ANN index later if needed.

        The scope is the load-bearing part rather than an afterthought. What
        comes back here is fed to the model as grounding, so an unscoped
        candidate set would quote one person's code into another person's
        generation — not a listing leak but their actual work, arriving as if
        the engine had thought of it.
        """
        entries = await self.list_kb_entries(owner=owner)
        scored = [(e, cosine_similarity(embedding, e.embedding)) for e in entries]
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:top_k]

    # ---- housekeeping ---------------------------------------
    async def sweep_orphans(self, *, dry_run: bool = False, grace_days: float = 0.0) -> list[str]:
        """Delete artifact blob files with no referencing row.

        Only files older than ``grace_days`` are eligible (0 = no grace). With
        ``dry_run`` the orphans are listed but not deleted. Empty version dirs are
        removed after a real sweep.
        """
        referenced = await self.all_artifact_paths()
        cutoff = time.time() - grace_days * 86400
        orphans: list[str] = []
        if not self.artifacts_dir.exists():
            return orphans
        for f in self.artifacts_dir.rglob("*"):
            if f.is_file() and str(f) not in referenced and f.stat().st_mtime <= cutoff:
                orphans.append(str(f))
                if not dry_run:
                    f.unlink()
        if not dry_run:
            for d in sorted((p for p in self.artifacts_dir.rglob("*") if p.is_dir()), reverse=True):
                try:
                    d.rmdir()
                except OSError:
                    pass  # not empty
        return orphans


class _ConfiguredConnection:
    """Async context manager that opens a connection with row+FK setup."""

    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        db = await self._conn
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys=ON")
        self._db = db
        return db

    async def __aexit__(self, *exc):
        await self._db.close()


def _project(r) -> Project:
    return Project(
        r["id"],
        r["name"],
        r["created_at"],
        r["updated_at"],
        r["current_version_id"],
        r["branched_from_version_id"],
        r["derived_from_project_id"],
        r["catalog_item_id"],
        # Falls back rather than trusting the column, because a row read through
        # a connection older than the migration would otherwise put None into a
        # field typed str and fail somewhere further away than here.
        r["owner"] or LOCAL.key,
    )


def _plugin_record(stored: object) -> dict | None:
    """One plugin record out of the database, or nothing when it is not one.

    A build writes this and the same build reads it back, so a bad value here
    means that build wrote something it could not have meant — but this is still
    the boundary where a project listing would otherwise fail whole over one
    row, and the caller cannot tell a corrupt record from a missing one anyway.
    Both answer ``None``.
    """
    if not isinstance(stored, str):
        return None
    try:
        loaded = json.loads(stored)
    except json.JSONDecodeError:
        logger.warning("a plugin record could not be read back and was answered as absent")
        return None
    return loaded if isinstance(loaded, dict) else None


async def _carry_publish_forward(db) -> None:
    """Move the old publish columns into the per-plugin table, once and for all.

    Runs on every ``init()`` while the columns still exist, so it has to be
    idempotent and it has to lose to a later write: `INSERT ... ON CONFLICT DO
    NOTHING` leaves alone any project a build has since recorded against itself.
    Without that, every restart would drop the current record back to whatever
    the columns held before this change.

    A row whose meta cannot be read still carries its address forward. The
    address is the part that cannot be recovered by asking anywhere else, and
    dropping the whole record because the smaller half is unreadable would lose
    the larger one for no gain.

    ``updated_at`` is the project's, not the publish's — the old schema never
    recorded when a publish happened, so there is no better value and this one
    must not be read as one. It also means the one case this does lose cannot be
    resolved by comparison afterwards: a publish made by *older* code after an
    upgrade, then upgraded again, keeps the newer plugin row and drops the
    columns' newer values. That is the accepted cost of losing to a later write,
    and a downgrade is not a path this supports.
    """
    carried = await (
        await db.execute(
            "SELECT id, published_slug, publish_meta_json, updated_at FROM projects"
            " WHERE published_slug IS NOT NULL"
        )
    ).fetchall()
    for row in carried:
        record: dict[str, Any] = {"slug": row["published_slug"]}
        meta = _plugin_record(row["publish_meta_json"])
        if meta is not None:
            record["meta"] = meta
        await db.execute(
            "INSERT INTO project_plugin_data(project_id, plugin, data_json, updated_at)"
            " VALUES (?,?,?,?) ON CONFLICT(project_id, plugin) DO NOTHING",
            (row["id"], LEGACY_PUBLISH_PLUGIN, json.dumps(record), row["updated_at"]),
        )


def _version(r) -> ScriptVersion:
    bbox = tuple(json.loads(r["bbox_json"])) if r["bbox_json"] else None
    params = json.loads(r["parameters_json"]) if r["parameters_json"] else {}
    return ScriptVersion(
        r["id"],
        r["project_id"],
        r["prompt"],
        r["code"],
        bool(r["ok"]),
        r["error"],
        r["volume"],
        bbox,
        r["created_at"],
        params,
        r["parent_version_id"],
        r["candidate_of_version_id"],
        r["plan_step"],
    )


def _artifact(r) -> Artifact:
    return Artifact(r["id"], r["version_id"], r["kind"], r["path"], r["bytes"])


def _session(r) -> ChatSession:
    return ChatSession(r["id"], r["project_id"], r["created_at"], r["updated_at"])


def _message(r) -> ChatMessage:
    return ChatMessage(
        r["id"],
        r["session_id"],
        r["seq"],
        r["role"],
        r["content"],
        r["status"],
        r["error"],
        r["version_id"],
        r["created_at"],
        _blocks_from_json(r["blocks_json"]),
    )


def _kb_entry(r) -> KBEntry:
    params = json.loads(r["params_json"]) if r["params_json"] else {}
    signature = json.loads(r["signature_json"]) if r["signature_json"] else {}
    provenance = json.loads(r["provenance_json"]) if r["provenance_json"] else {}
    embedding = json.loads(r["embedding_json"])
    return KBEntry(
        r["id"],
        r["nl_intent"],
        r["code"],
        embedding,
        r["created_at"],
        params,
        signature,
        provenance,
        r["owner"] or LOCAL.key,
    )


_default_store: Store | None = None


def get_store() -> Store:
    """Process-wide Store built from settings (used by the API layer)."""
    global _default_store
    if _default_store is None:
        _default_store = Store()
    return _default_store
