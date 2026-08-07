"""A corrupt sidecar ledger must not take the rest of the app down with it.

Every route that could mutate a project used to read the ledger to decide whether
to refuse, which made an unreadable copy of it everyone's problem: ordinary
projects' writes, the project list and chat all answered 500, with the JSON
parser's own message in the body. Resolving it to an empty ledger instead would
have been worse — catalog items would have gone editable and the next start would
have imported the whole catalog a second time.

So the mark moved onto the project row (``projects.catalog_item_id``) and the
ledger kept what the catalog panel displays. These pin both halves: what a bad
ledger no longer costs, and what it still does.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import _autoload_catalog, create_app
from cadless.catalog.importer import imported_domain_dir
from cadless.catalog.ledger import Ledger, LedgerUnreadable
from cadless.catalog.loader import (
    backfill_catalog_item_ids,
    clear_all,
    clear_house,
    load_house,
)
from cadless.config import settings
from cadless.store import Store
from tests.catalog_helpers import load_catalog_item

# Half of an entry: what a crash partway through a write leaves behind.
TRUNCATED = '{"cat-1": {"project_id": 1, "step_c'

# The parser's own words. If they reach a client, the exception got out.
PARSER_WORDS = ("Expecting value", "Unterminated", "Expecting ',' delimiter")


@pytest.fixture(autouse=True)
def _own_catalog_roots(tmp_path, monkeypatch):
    # The lifespan auto-load would inject the bundled samples into stores these
    # tests assert the exact contents of. `data_dir` is the other root it walks,
    # and leaving it alone pointed these tests at whatever the developer running
    # them has received onto their own machine.
    monkeypatch.setattr(settings, "catalog_root", tmp_path / "catalog")
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    (tmp_path / "data").mkdir()
    # Empty, but there: the app ships this root, so a missing one means a mount
    # that did not come back rather than a machine without a catalog — and the
    # removal route will not read a record as fileless while it cannot see it.
    (tmp_path / "catalog").mkdir()


@pytest.fixture
def store(tmp_path):
    return Store(db_path=tmp_path / "db.sqlite", artifacts_dir=tmp_path / "artifacts")


@pytest.fixture
def client(store):
    # The app answers a stray exception with a 500 of its own, so let the client
    # hand back that response instead of re-raising it into the test.
    with TestClient(create_app(store=store), raise_server_exceptions=False) as c:
        yield c


def ledger_path(store: Store) -> Path:
    return Path(store.db_path).parent / "catalog-ledger.json"


def corrupt_ledger(store: Store) -> None:
    """Leave a half-written ledger where the catalog looks for it."""
    path = ledger_path(store)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(TRUNCATED)


def leaked(response) -> bool:
    return any(word in response.text for word in PARSER_WORDS)


def make_ledger_legacy(store: Store, house_id: str, project_id: int) -> None:
    """Put a ``project_id`` back into an entry, as versions before the column did.

    The back-fill exists for exactly those ledgers, so a test that writes one with
    today's code would be testing nothing.
    """
    path = ledger_path(store)
    entries = json.loads(path.read_text())
    entries[house_id]["project_id"] = project_id
    path.write_text(json.dumps(entries))


def _seed_current_version(store: Store, pid: int) -> int:
    async def go() -> int:
        v = await store.add_version(pid, "v", "code", True)
        await store.set_current_version(pid, v.id)
        return v.id

    return asyncio.run(go())


# --- the ledger says so rather than guessing ---------------------------------


def test_reading_a_corrupt_ledger_raises_instead_of_reading_as_empty(tmp_path):
    """ "Cannot tell" and "nothing is loaded" lead opposite ways, so they differ."""
    path = tmp_path / "ledger.json"
    path.write_text(TRUNCATED)
    with pytest.raises(LedgerUnreadable):
        Ledger(path).entries()


def test_a_ledger_holding_the_wrong_shape_is_unreadable_too(tmp_path):
    """A valid-JSON list would fail later, further from the cause than here."""
    path = tmp_path / "ledger.json"
    path.write_text('["cat-1"]')
    with pytest.raises(LedgerUnreadable):
        Ledger(path).entries()


def test_an_absent_or_empty_ledger_is_simply_empty(tmp_path):
    """Only a file that is there and unusable is an error."""
    assert Ledger(tmp_path / "nope.json").entries() == {}
    (tmp_path / "blank.json").write_text("  \n")
    assert Ledger(tmp_path / "blank.json").entries() == {}


def test_quarantine_keeps_the_bad_copy_and_frees_the_path(tmp_path):
    """The bad file is evidence, so it is moved rather than deleted."""
    path = tmp_path / "ledger.json"
    path.write_text(TRUNCATED)
    aside = Ledger(path).quarantine()
    assert aside is not None and aside.exists()
    assert aside.read_text() == TRUNCATED
    assert not path.exists()
    assert Ledger(path).entries() == {}  # a fresh one can be written now


# --- what a bad ledger no longer costs --------------------------------------


def test_revert_on_an_ordinary_project_survives_a_corrupt_ledger(client, store):
    """A project the catalog never touched must still be revertable."""
    pid = client.post("/projects", json={"name": "P"}).json()["id"]
    version_id = _seed_current_version(store, pid)
    corrupt_ledger(store)

    r = client.post(f"/projects/{pid}/revert", json={"version_id": version_id})

    assert r.status_code == 200, f"corrupt ledger broke a valid revert: {r.text}"
    assert not leaked(r)


def test_every_route_that_gates_on_catalog_membership_survives(client, store):
    """One corrupt file, and none of the routes that used to die.

    Asserted together because the point is the blast radius rather than any single
    route: this file used to be read by the project list, project detail, revert
    and rerun, none of which are about catalog items.
    """
    pid = client.post("/projects", json={"name": "P"}).json()["id"]
    version_id = _seed_current_version(store, pid)

    calls = {
        "GET /projects": lambda: client.get("/projects"),
        "GET /projects/{id}": lambda: client.get(f"/projects/{pid}"),
        "GET /catalog": lambda: client.get("/catalog"),
        "POST /projects/{id}/revert": lambda: client.post(
            f"/projects/{pid}/revert", json={"version_id": version_id}
        ),
        "POST /versions/{id}/rerun": lambda: client.post(f"/versions/{version_id}/rerun"),
    }
    healthy = {name: call().status_code for name, call in calls.items()}

    corrupt_ledger(store)

    broke = {}
    for name, call in calls.items():
        r = call()
        if r.status_code != healthy[name] or leaked(r):
            broke[name] = f"{healthy[name]} -> {r.status_code}"
    assert not broke, f"a corrupt ledger changed these: {broke}"


def test_a_catalog_item_stays_read_only_through_a_corrupt_ledger(client, store, tmp_path):
    """The refusal is the whole reason the mark exists, so it must outlive the file.

    Reading the ledger as empty would have made this a 200 — the user's edit
    landing on the pristine baseline instead of on their own copy of it.
    """
    pid = load_catalog_item(store, tmp_path / "cat")
    first = client.get(f"/projects/{pid}/versions").json()[0]["id"]
    corrupt_ledger(store)

    r = client.post(f"/projects/{pid}/revert", json={"version_id": first})

    assert r.status_code == 403
    assert "read-only" in r.json()["detail"].lower()
    assert client.get(f"/projects/{pid}").json()["is_catalog"] is True


def test_rename_and_delete_stay_refused_through_a_corrupt_ledger(client, store, tmp_path):
    """The gates that cover rename and delete read the same mark as the rest.

    Those two are the ones a wrong answer cannot be taken back from — a renamed
    item reads differently depending on where you look, and a deleted one does not
    come back — so they must not be the pair that a damaged file unlocks.
    """
    pid = load_catalog_item(store, tmp_path / "cat")
    corrupt_ledger(store)

    renamed = client.patch(f"/projects/{pid}", json={"name": "mine now"})
    deleted = client.delete(f"/projects/{pid}")

    assert renamed.status_code == 403, renamed.text
    assert deleted.status_code == 403, deleted.text
    assert "read-only" in renamed.json()["detail"].lower()
    # Still there, still named what the catalog named it.
    assert client.get(f"/projects/{pid}").json()["name"] == "Read Only Item"


def test_removing_a_bundled_item_is_still_refused_through_a_corrupt_ledger(client, store, tmp_path):
    """`DELETE /catalog/{id}` finds the item without the ledger, then refuses it.

    404 would be the wrong answer — the item is plainly here — and 500 would be
    the old one. A bundled item earns 403 because the root its files are in is
    walked at every start, so removing it would not outlast one.

    Seeded into `settings.catalog_root` rather than beside it: that is what makes
    it a bundled item, and an item written outside both roots would be a record
    with no files, which this route clears rather than refuses.
    """
    load_catalog_item(store, settings.domain_catalog_dir("house"))
    corrupt_ledger(store)

    r = client.delete("/catalog/cat-1")

    assert r.status_code == 403, r.text
    assert "would not outlast the next start" in r.json()["detail"]
    assert client.delete("/catalog/no-such-item").status_code == 404


def test_the_listing_prefers_the_received_copy_of_an_id_both_roots_hold(client, store, tmp_path):
    """One id, two directories: a sample in the startup catalog and a package
    received earlier. Only the received copy is this app's to take away, so the
    panel has to offer the removal — and the route would perform it."""
    load_catalog_item(store, imported_domain_dir("house"))
    # The same id in the startup catalog, written rather than loaded: one id can
    # only be one project, so the second copy is a directory the loader passes
    # over, not a second item.
    sample = settings.domain_catalog_dir("house") / "cat-1"
    (sample / "steps").mkdir(parents=True)
    (sample / "manifest.json").write_text(
        json.dumps({"id": "cat-1", "name": "Read Only Item", "domain": "house", "steps": []})
    )

    item = next(it for it in client.get("/catalog").json()["items"] if it["house_id"] == "cat-1")

    assert item["removable"] is True
    assert item["files_missing"] is False
    assert item["source"] is None  # it arrived, and its provenance says nothing


def test_a_record_with_no_files_is_cleared_through_a_corrupt_ledger(client, store, tmp_path):
    """The entry cannot be dropped, and the item is cleared anyway.

    `clear_house` takes the project row first and treats an unreadable ledger as
    an entry it could not drop, which is the right order here: the row is what
    decides read-only access and what refuses the next import of this name, and
    the entry left behind is display metadata the next load overwrites.
    """
    pid = load_catalog_item(store, tmp_path / "gone")
    shutil.rmtree(tmp_path / "gone")
    corrupt_ledger(store)

    r = client.delete("/catalog/cat-1")

    assert r.status_code == 204, r.text
    assert asyncio.run(store.get_project(pid)) is None


def test_a_corrupt_ledger_does_not_import_the_catalog_a_second_time(store, tmp_path):
    """What made "treat it as empty" unaffordable.

    The loader asks the db whether an item is already in, so a ledger it cannot
    read cannot report every item as absent and load a duplicate of each.
    """
    house = tmp_path / "cat" / "cat-1"
    load_catalog_item(store, tmp_path / "cat")
    corrupt_ledger(store)

    async def load_again():
        return await load_house(store, Ledger(ledger_path(store)), house)

    assert asyncio.run(load_again()) is None  # skipped, not re-imported
    assert len(asyncio.run(store.list_projects())) == 1


def test_clearing_an_item_works_when_the_ledger_cannot_be_read(store, tmp_path):
    """A loaded item must stay removable, or a reload adds a second copy of it."""
    pid = load_catalog_item(store, tmp_path / "cat")
    corrupt_ledger(store)

    cleared = asyncio.run(clear_house(store, Ledger(ledger_path(store)), "cat-1"))

    assert cleared is True
    assert asyncio.run(store.get_project(pid)) is None
    assert asyncio.run(store.catalog_item_ids()) == {}


def test_clear_all_reaches_items_the_ledger_cannot_name(store, tmp_path):
    """Clearing everything walks the db, so an unreadable ledger hides nothing."""
    load_catalog_item(store, tmp_path / "cat")
    corrupt_ledger(store)

    assert asyncio.run(clear_all(store, Ledger(ledger_path(store)))) == ["cat-1"]
    assert asyncio.run(store.list_projects()) == []


def test_no_500_response_carries_the_parser_message(client, store):
    """Even where something does fail, the internals stay out of the body."""
    pid = client.post("/projects", json={"name": "P"}).json()["id"]
    _seed_current_version(store, pid)
    corrupt_ledger(store)
    # `record` cannot read-modify-write a ledger it cannot parse, so this is a
    # path that still fails — it just must not narrate why to the caller.
    with pytest.raises(LedgerUnreadable):
        Ledger(ledger_path(store)).record("cat-9", 1)
    r = client.get(f"/projects/{pid}")
    assert r.status_code == 200 and not leaked(r)


# --- the catalog panel: contents survive, details do not ---------------------


def test_the_catalog_still_lists_its_items_without_their_details(client, store, tmp_path):
    """Losing the ledger costs the panel its decoration, not its contents."""
    load_catalog_item(store, tmp_path / "cat")
    before = client.get("/catalog").json()
    assert [it["house_id"] for it in before["items"]] == ["cat-1"]
    assert before["details_unavailable"] is False

    corrupt_ledger(store)
    after = client.get("/catalog").json()

    assert after["total"] == 1
    assert [it["house_id"] for it in after["items"]] == ["cat-1"]
    assert after["items"][0]["name"] == "Read Only Item"  # from the project row
    assert after["details_unavailable"] is True, "a stripped catalog must say so"


def test_details_unavailable_is_false_when_a_catalog_is_simply_empty(client):
    """No items is not the same as items whose details went missing."""
    body = client.get("/catalog").json()
    assert body["items"] == [] and body["details_unavailable"] is False


def test_two_projects_cannot_claim_the_same_catalog_item(store):
    """The duplicate this column exists to prevent is refused by the db itself."""

    async def go():
        await store.init()
        await store.create_project("First", catalog_item_id="cat-1")
        await store.create_project("Second", catalog_item_id="cat-1")

    with pytest.raises(sqlite3.IntegrityError):
        asyncio.run(go())


def test_ordinary_projects_do_not_collide_on_their_empty_mark(store):
    """The uniqueness is partial: every user project leaves the column NULL."""

    async def go():
        await store.init()
        await store.create_project("A")
        await store.create_project("B")
        return await store.list_projects()

    assert len(asyncio.run(go())) == 2


def test_loading_succeeds_even_when_the_details_cannot_be_recorded(store, tmp_path):
    """A metadata write it cannot do must not undo an item it already loaded."""
    house = tmp_path / "cat" / "cat-1"
    (house / "steps").mkdir(parents=True)
    (house / "steps" / "01.py").write_text("result = 1\n")
    (house / "manifest.json").write_text(
        json.dumps(
            {
                "id": "cat-1",
                "name": "Item",
                "domain": "house",
                "steps": [{"index": 1, "instruction": "s1", "code": "steps/01.py"}],
            }
        )
    )
    corrupt_ledger(store)  # record() has to read before it writes, and cannot

    async def go():
        await store.init()
        return await load_house(store, Ledger(ledger_path(store)), house)

    pid = asyncio.run(go())

    assert pid is not None, "the item was loaded; only its details were not recorded"
    assert asyncio.run(store.catalog_item_ids()) == {pid: "cat-1"}  # and it is read-only


# --- migrating a database from before the column -----------------------------


def test_backfill_marks_catalog_projects_from_the_ledger(store, tmp_path):
    """A db loaded before the column keeps the mapping only in the ledger."""
    pid = load_catalog_item(store, tmp_path / "cat")
    asyncio.run(store.set_catalog_item_id(pid, None))  # as a pre-column row looks
    make_ledger_legacy(store, "cat-1", pid)
    assert asyncio.run(store.catalog_item_ids()) == {}

    marked = asyncio.run(backfill_catalog_item_ids(store, Ledger(ledger_path(store))))

    assert marked == 1
    assert asyncio.run(store.catalog_item_ids()) == {pid: "cat-1"}


def test_backfill_leaves_a_clone_editable(store, tmp_path):
    """Only the row the ledger names is marked — a user's copy is not one."""
    pid = load_catalog_item(store, tmp_path / "cat")
    clone = asyncio.run(store.clone_project(pid))
    asyncio.run(store.set_catalog_item_id(pid, None))
    make_ledger_legacy(store, "cat-1", pid)

    assert asyncio.run(backfill_catalog_item_ids(store, Ledger(ledger_path(store)))) == 1

    assert asyncio.run(store.get_project(pid)).catalog_item_id == "cat-1"
    assert asyncio.run(store.get_project(clone.id)).catalog_item_id is None


def test_backfill_is_a_no_op_on_a_ledger_written_since_the_column(store, tmp_path):
    """Today's ledger holds no project ids, and the rows already carry the mark."""
    pid = load_catalog_item(store, tmp_path / "cat")

    assert asyncio.run(backfill_catalog_item_ids(store, Ledger(ledger_path(store)))) == 0
    assert asyncio.run(store.catalog_item_ids()) == {pid: "cat-1"}


def test_startup_refuses_to_guess_when_neither_source_can_answer(store, tmp_path, caplog):
    """A pre-column db plus an unreadable ledger: the one case with no answer.

    Calling them all ordinary projects would unlock every catalog item, and
    loading would add a second copy of each. It does neither, and says so.
    """
    pid = load_catalog_item(store, tmp_path / "cat")
    asyncio.run(store.set_catalog_item_id(pid, None))  # pre-column shape
    corrupt_ledger(store)

    asyncio.run(_autoload_catalog(store))

    assert asyncio.run(store.catalog_item_ids()) == {}  # nothing guessed
    assert len(asyncio.run(store.list_projects())) == 1  # nothing duplicated
    assert not ledger_path(store).exists()  # moved aside
    assert list(ledger_path(store).parent.glob("catalog-ledger.json.corrupt-*"))
    assert "will not guess" in caplog.text


def test_startup_carries_on_when_the_db_already_knows(store, tmp_path, caplog):
    """With the marks on the rows, a lost ledger costs only display detail."""
    load_catalog_item(store, tmp_path / "cat")
    corrupt_ledger(store)

    asyncio.run(_autoload_catalog(store))

    assert list(asyncio.run(store.catalog_item_ids()).values()) == ["cat-1"]
    assert len(asyncio.run(store.list_projects())) == 1
    assert list(ledger_path(store).parent.glob("catalog-ledger.json.corrupt-*"))
