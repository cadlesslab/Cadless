"""Two owners, one database, and nothing of one reaching the other.

Every scoped method gets its own assertion rather than a sample, because "most
of them" is the shape this defect takes: one method left unscoped is a whole
class of row readable by anybody who can guess an integer. `test_store_surface`
holds the *list* honest; this file holds each entry on it honest.

The bare-id methods matter most. A version, an artifact, a chat session and a
message are all addressed without a project anywhere in sight, so the only thing
between a guessed integer and somebody else's work is the join these make.
"""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from pathlib import Path

import pytest

from cadless.identity import SYSTEM_KEY
from cadless.store import Store

A = "user-a"
B = "user-b"


def run(coro):
    return asyncio.run(coro)


@dataclass
class World:
    """One owner's project, fully populated, plus the ids to reach into it."""

    project_id: int
    version_id: int
    candidate_id: int
    session_id: int
    message_id: int
    kb_id: int


async def _seed(s: Store, owner: str, tmp_path: Path, tag: str) -> World:
    p = await s.create_project(f"{tag} project", owner=owner)
    v = await s.add_version(p.id, "make a bracket", "code", True, owner=owner)
    loser = await s.add_version(
        p.id, "another try", "code2", True, candidate_of_version_id=v.id, owner=owner
    )
    blob = tmp_path / f"{tag}.step"
    blob.write_text(tag)
    await s.add_artifact(v.id, "step", str(blob), owner=owner)
    thumb = tmp_path / f"{tag}.png"
    thumb.write_text(tag)
    await s.add_artifact(v.id, "thumbnail", str(thumb), owner=owner)
    await s.set_current_version(p.id, v.id, owner=owner)
    await s.record_plugin_data(p.id, "publish", {"slug": tag}, owner=owner)
    session = await s.get_or_create_session(p.id, owner=owner)
    m = await s.add_message(session.id, "user", f"{tag} hello", owner=owner)
    kb = await s.add_kb_entry(f"{tag} intent", "kb code", [1.0, 0.0], owner=owner)
    return World(p.id, v.id, loser.id, session.id, m.id, kb.id)


@pytest.fixture
def two_owners(tmp_path):
    """A and B each with a full project, plus one catalogue item owned by the build."""
    s = Store(db_path=tmp_path / "db.sqlite", artifacts_dir=tmp_path / "artifacts")

    async def go():
        await s.init()
        a = await _seed(s, A, tmp_path, "a")
        b = await _seed(s, B, tmp_path, "b")
        shipped = await s.create_project(
            "Demo house", catalog_item_id="house/demo", owner=SYSTEM_KEY
        )
        return a, b, shipped.id

    a, b, shipped_id = run(go())
    return s, a, b, shipped_id


# ---- listings ---------------------------------------------------------------


def test_a_listing_shows_only_your_projects_and_the_builds(two_owners):
    s, a, b, shipped_id = two_owners

    async def go():
        return [p.id for p in await s.list_projects(owner=A)]

    ids = run(go())
    assert a.project_id in ids
    assert shipped_id in ids, "the bundled catalogue is readable by every principal"
    assert b.project_id not in ids


def test_an_unscoped_caller_still_sees_everything(two_owners):
    # Housekeeping and the CLI run outside any request and must not be filtered.
    s, a, b, shipped_id = two_owners

    async def go():
        return [p.id for p in await s.list_projects()]

    assert set(run(go())) == {a.project_id, b.project_id, shipped_id}


def test_catalog_item_ids_is_scoped(two_owners):
    s, _a, _b, shipped_id = two_owners

    async def go():
        # A holds an item of their own as well as the one that shipped.
        mine = await s.create_project("Widget", catalog_item_id="item/widget", owner=A)
        return mine.id, await s.catalog_item_ids(owner=A), await s.catalog_item_ids(owner=B)

    a_item_id, for_a, for_b = run(go())
    assert for_a == {shipped_id: "house/demo", a_item_id: "item/widget"}
    # B sees the build's item and none of A's.
    assert for_b == {shipped_id: "house/demo"}


def test_list_kb_entries_is_scoped(two_owners):
    s, a, b, _ = two_owners

    async def go():
        return [e.id for e in await s.list_kb_entries(owner=A)]

    ids = run(go())
    assert ids == [a.kb_id]
    assert b.kb_id not in ids


def test_grounding_retrieval_never_returns_another_owners_code(two_owners):
    """The RAG path. What comes back here is quoted to the model as context."""
    s, a, b, _ = two_owners

    async def go():
        return await s.query_kb_by_vector([1.0, 0.0], top_k=10, owner=A)

    hits = run(go())
    assert [e.id for e, _score in hits] == [a.kb_id]
    assert all(e.id != b.kb_id for e, _score in hits)


def test_thumbnail_version_ids_is_scoped(two_owners):
    s, a, b, _ = two_owners

    async def go():
        # Ask for both, as a caller that assembled ids from somewhere else would.
        return await s.thumbnail_version_ids([a.project_id, b.project_id], owner=A)

    found = run(go())
    assert a.project_id in found
    assert b.project_id not in found


def test_plugin_data_for_ignores_ids_you_do_not_own(two_owners):
    s, a, b, _ = two_owners

    async def go():
        return await s.plugin_data_for([a.project_id, b.project_id], "publish", owner=A)

    found = run(go())
    assert found == {a.project_id: {"slug": "a"}}


# ---- single-row reads, addressed by id --------------------------------------


def test_get_project_hides_another_owners(two_owners):
    s, _a, b, _ = two_owners

    async def go():
        return await s.get_project(b.project_id, owner=A)

    assert run(go()) is None


def test_plugin_data_hides_another_owners(two_owners):
    s, _a, b, _ = two_owners

    async def go():
        return await s.plugin_data(b.project_id, "publish", owner=A)

    assert run(go()) is None


def test_list_versions_hides_another_owners(two_owners):
    s, _a, b, _ = two_owners

    async def go():
        return await s.list_versions(b.project_id, owner=A)

    assert run(go()) == []


def test_get_version_hides_another_owners(two_owners):
    """A bare version id is the cheapest thing on the API to guess."""
    s, _a, b, _ = two_owners

    async def go():
        return await s.get_version(b.version_id, owner=A)

    assert run(go()) is None


def test_list_candidate_versions_hides_another_owners(two_owners):
    s, _a, b, _ = two_owners

    async def go():
        return await s.list_candidate_versions(b.version_id, owner=A)

    assert run(go()) == []


def test_last_ok_version_hides_another_owners(two_owners):
    s, _a, b, _ = two_owners

    async def go():
        return await s.last_ok_version(b.project_id, owner=A)

    assert run(go()) is None


def test_list_artifacts_hides_another_owners(two_owners):
    s, _a, b, _ = two_owners

    async def go():
        return await s.list_artifacts(b.version_id, owner=A)

    assert run(go()) == []


def test_get_artifact_hides_another_owners(two_owners):
    """This one hands back file bytes, so it is the narrowest leak of the lot."""
    s, _a, b, _ = two_owners

    async def go():
        return await s.get_artifact(b.version_id, "step", owner=A)

    assert run(go()) is None


def test_project_id_for_catalog_item_does_not_hand_over_another_owners_copy(tmp_path):
    # Unscoped, the second importer is told the item is already held and given
    # the first importer's project.
    s = Store(db_path=tmp_path / "db.sqlite", artifacts_dir=tmp_path / "artifacts")

    async def go():
        await s.init()
        mine = await s.create_project("Widget", catalog_item_id="item/widget", owner=A)
        return mine.id, await s.project_id_for_catalog_item("item/widget", owner=B)

    a_id, seen_by_b = run(go())
    assert a_id is not None
    assert seen_by_b is None


def test_get_kb_entry_hides_another_owners(two_owners):
    s, _a, b, _ = two_owners

    async def go():
        return await s.get_kb_entry(b.kb_id, owner=A)

    assert run(go()) is None


def test_list_messages_hides_another_owners_transcript(two_owners):
    s, _a, b, _ = two_owners

    async def go():
        return await s.list_messages(b.session_id, owner=A)

    assert run(go()) == []


# ---- writes -----------------------------------------------------------------


def test_rename_refuses_another_owners_project(two_owners):
    s, _a, b, _ = two_owners

    async def go():
        refused = await s.rename_project(b.project_id, "taken", owner=A)
        still = await s.get_project(b.project_id, owner=B)
        return refused, still.name

    refused, name = run(go())
    assert refused is None
    assert name == "b project"


def test_delete_refuses_another_owners_project_and_keeps_its_files(two_owners):
    s, _a, b, _ = two_owners

    async def go():
        refused = await s.delete_project(b.project_id, owner=A)
        survives = await s.get_project(b.project_id, owner=B)
        artifacts = await s.list_artifacts(b.version_id, owner=B)
        return refused, survives, artifacts

    refused, survives, artifacts = run(go())
    assert refused is False
    assert survives is not None
    # The blob directories must still be there: a refused delete that removed
    # the files would destroy the data it just declined to own.
    assert [Path(x.path).exists() for x in artifacts] == [True, True]


def test_marking_a_project_as_a_catalogue_item_is_out_of_a_routes_reach(two_owners):
    """Not scoped — removed from the request surface instead.

    Declaring a project a catalogue item files it under the build, which is what
    a catalogue item is. That makes it a way to hand your own project to every
    principal, so a route cannot call it at all rather than calling it safely.
    """
    from cadless.scoped_store import ScopedStore

    assert not hasattr(ScopedStore, "set_catalog_item_id")


def test_record_plugin_data_refuses_another_owners_project(two_owners):
    s, _a, b, _ = two_owners

    async def go():
        wrote = await s.record_plugin_data(b.project_id, "publish", {"slug": "hijack"}, owner=A)
        theirs = await s.plugin_data(b.project_id, "publish", owner=B)
        return wrote, theirs

    wrote, theirs = run(go())
    assert wrote is False
    assert theirs == {"slug": "b"}


def test_add_version_refuses_another_owners_project(two_owners):
    s, _a, b, _ = two_owners

    async def go():
        await s.add_version(b.project_id, "injected", "code", True, owner=A)

    with pytest.raises(LookupError):
        run(go())


def test_add_artifact_refuses_another_owners_version(two_owners, tmp_path):
    s, _a, b, _ = two_owners
    blob = tmp_path / "injected.step"
    blob.write_text("x")

    async def go():
        await s.add_artifact(b.version_id, "step", str(blob), owner=A)

    with pytest.raises(LookupError):
        run(go())


def test_set_current_version_refuses_another_owners_project(two_owners):
    s, _a, b, _ = two_owners

    async def go():
        return await s.set_current_version(b.project_id, b.candidate_id, owner=A)

    assert run(go()) is False


def test_get_or_create_session_refuses_another_owners_project(two_owners):
    s, _a, b, _ = two_owners

    async def go():
        await s.get_or_create_session(b.project_id, owner=A)

    with pytest.raises(LookupError):
        run(go())


def test_add_message_refuses_another_owners_session(two_owners):
    s, _a, b, _ = two_owners

    async def go():
        await s.add_message(b.session_id, "user", "injected", owner=A)

    with pytest.raises(LookupError):
        run(go())


def test_update_message_refuses_another_owners_message(two_owners):
    s, _a, b, _ = two_owners

    async def go():
        refused = await s.update_message(b.message_id, content="rewritten", owner=A)
        theirs = await s.list_messages(b.session_id, owner=B)
        return refused, theirs[0].content

    refused, content = run(go())
    assert refused is None
    assert content == "b hello"


# ---- reading the build's rows is not permission to change them ---------------


@pytest.fixture
def shared_but_not_catalogue(tmp_path):
    """A row the build owns that is *not* a catalogue item.

    The router refuses mutations on a catalogue project, so a catalogue item
    would test that guard rather than this one. This has no `catalog_item_id`,
    which is precisely the row that guard says nothing about.
    """
    s = Store(db_path=tmp_path / "db.sqlite", artifacts_dir=tmp_path / "artifacts")

    async def go():
        await s.init()
        p = await s.create_project("The build's own", owner=SYSTEM_KEY)
        v = await s.add_version(p.id, "seed", "code", True, owner=SYSTEM_KEY)
        return p.id, v.id

    pid, vid = run(go())
    return s, pid, vid


def test_you_can_read_the_builds_row(shared_but_not_catalogue):
    s, pid, _vid = shared_but_not_catalogue

    async def go():
        return await s.get_project(pid, owner=A)

    assert run(go()) is not None, "the build's rows are readable by everybody — that is the point"


def test_you_cannot_rename_the_builds_row(shared_but_not_catalogue):
    s, pid, _vid = shared_but_not_catalogue

    async def go():
        refused = await s.rename_project(pid, "mine now", owner=A)
        return refused, (await s.get_project(pid)).name

    refused, name = run(go())
    assert refused is None
    assert name == "The build's own"


def test_you_cannot_delete_the_builds_row(shared_but_not_catalogue):
    s, pid, _vid = shared_but_not_catalogue

    async def go():
        refused = await s.delete_project(pid, owner=A)
        return refused, await s.get_project(pid)

    refused, still = run(go())
    assert refused is False
    assert still is not None


def test_you_cannot_add_to_the_builds_row(shared_but_not_catalogue):
    s, pid, _vid = shared_but_not_catalogue

    async def go():
        await s.add_version(pid, "injected", "code", True, owner=A)

    with pytest.raises(LookupError):
        run(go())


def test_you_cannot_start_a_transcript_on_the_builds_row(shared_but_not_catalogue):
    s, pid, _vid = shared_but_not_catalogue

    async def go():
        # Delete the session the build's own create made, so this is a create
        # rather than a read of one that is already there.
        import sqlite3

        con = sqlite3.connect(s.db_path)
        con.execute("DELETE FROM chat_sessions WHERE project_id=?", (pid,))
        con.commit()
        con.close()
        await s.get_or_create_session(pid, owner=A)

    with pytest.raises(LookupError):
        run(go())


def test_you_cannot_move_the_builds_current_version(shared_but_not_catalogue):
    s, pid, vid = shared_but_not_catalogue

    async def go():
        return await s.set_current_version(pid, vid, owner=A)

    assert run(go()) is False


def test_you_cannot_record_against_the_builds_row(shared_but_not_catalogue):
    s, pid, _vid = shared_but_not_catalogue

    async def go():
        return await s.record_plugin_data(pid, "publish", {"slug": "hijack"}, owner=A)

    assert run(go()) is False


def test_but_the_build_itself_still_can(shared_but_not_catalogue):
    # The narrowing must not lock the installation out of its own rows, or the
    # catalogue could never be loaded or cleared.
    s, pid, _vid = shared_but_not_catalogue

    async def go():
        renamed = await s.rename_project(pid, "renamed by the build", owner=SYSTEM_KEY)
        return renamed

    assert run(go()) is not None


def test_widening_to_the_build_is_the_only_way_in_and_is_unconditional(two_owners):
    """An accepted gap, pinned here so that changing it has to be deliberate.

    A request-scoped view cannot touch the build's rows — that is the split
    above. What can is the named widening, and it does not ask who wanted it:
    `load_house` and `clear_house` apply it to whatever store they were handed,
    which is what makes a catalogue item belong to the installation no matter
    which caller loaded it.

    The consequence is that the two routes reaching those functions —
    `POST /packages/import` and `DELETE /catalog/{id}` —
    curate shared content on behalf of whoever called them, and this engine has
    no notion of privilege to tell one caller from another. Recorded as the
    fourth known gap in ADR-0006 rather than closed here: deciding who may
    curate is a decision about roles, and roles are the plug, not the socket.
    A hosted build admitting untrusted callers gates or replaces those routes.
    """
    from cadless.identity import Principal
    from cadless.scoped_store import ScopedStore, system_view

    s, _a, _b, shipped_id = two_owners
    as_b = ScopedStore(s, Principal(B))

    async def go():
        readable = await as_b.get_project(shipped_id) is not None
        refused = await as_b.delete_project(shipped_id)
        widened = await system_view(as_b).delete_project(shipped_id)
        return readable, refused, widened

    readable, refused, widened = run(go())
    assert readable, "every principal reads the build's rows"
    assert refused is False, "and no principal writes them"
    assert widened is True, "except through the widening, which is deliberate and unguarded"


def test_only_the_builds_view_can_mint_or_mark_a_catalogue_item(tmp_path):
    """The two powers held back from the request-facing view."""
    from cadless.identity import Principal
    from cadless.scoped_store import ScopedStore, system_view

    s = Store(db_path=tmp_path / "db.sqlite", artifacts_dir=tmp_path / "artifacts")

    async def go():
        await s.init()
        build = system_view(s)
        made = await build.create_project("Shipped", catalog_item_id="house/demo")
        plain = await build.create_project("Not yet an item")
        await build.set_catalog_item_id(plain.id, "house/late")
        return made, await s.get_project(plain.id)

    made, marked = run(go())
    assert made.catalog_item_id == "house/demo"
    assert made.owner == SYSTEM_KEY
    # Marking files it under the build, which is what a catalogue item is.
    assert marked.catalog_item_id == "house/late"
    assert marked.owner == SYSTEM_KEY

    as_a = ScopedStore(s, Principal(A))
    assert not hasattr(as_a, "set_catalog_item_id")
    assert "catalog_item_id" not in inspect.signature(as_a.create_project).parameters


# ---- deriving from what you are allowed to read ------------------------------


def test_cloning_the_catalogue_gives_you_something_of_your_own(two_owners):
    """Customize-from-catalog: the source is the build's, the copy is yours."""
    s, _a, _b, shipped_id = two_owners

    async def go():
        clone = await s.clone_project(shipped_id, owner=A)
        return clone

    clone = run(go())
    assert clone is not None
    assert clone.owner == A
    assert clone.catalog_item_id is None, "a copy is editable, so it is not the catalogue item"


def test_you_cannot_clone_what_you_cannot_see(two_owners):
    s, _a, b, _ = two_owners

    async def go():
        return await s.clone_project(b.project_id, owner=A)

    assert run(go()) is None


def test_branching_gives_you_something_of_your_own(two_owners):
    s, a, _b, _ = two_owners

    async def go():
        return await s.branch_project(a.version_id, owner=A)

    branch = run(go())
    assert branch is not None
    assert branch.owner == A


def test_you_cannot_branch_from_a_version_you_cannot_see(two_owners):
    s, _a, b, _ = two_owners

    async def go():
        return await s.branch_project(b.version_id, owner=A)

    assert run(go()) is None


def test_a_clone_of_the_catalogue_does_not_copy_the_owner(two_owners):
    # Inheriting the source owner would produce another read-only row belonging
    # to the build, which is the opposite of what customizing means.
    s, _a, _b, shipped_id = two_owners

    async def go():
        clone = await s.clone_project(shipped_id, owner=A)
        return await s.get_project(clone.id, owner=A)

    mine = run(go())
    assert mine is not None
    assert mine.owner != SYSTEM_KEY
