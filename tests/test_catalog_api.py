"""GET /catalog endpoint tests (House Catalog browse + discovery, #21)."""

import asyncio
import contextlib
import json
import shutil
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.routers import catalog as catalog_router
from cadless.catalog.domains import all_domains
from cadless.catalog.importer import imported_domain_dir
from cadless.catalog.ledger import Ledger
from cadless.catalog.loader import load_house
from cadless.config import settings
from cadless.store import Store
from tests.cls_fixtures import packed
from tests.depot_origin import DEPOT_CATALOG_ID, DEPOT_DIGEST, DEPOT_SENTENCE


@pytest.fixture(autouse=True)
def _own_catalog_roots(tmp_path, monkeypatch):
    # Both roots the autoload walks are moved somewhere this test owns. The
    # bundled samples would otherwise be injected into the hand-seeded stores
    # these tests assert exact contents of, and a received item a developer
    # imported locally would turn up in these listings.
    #
    # Pointed at a directory the seeded items are then written into, rather than
    # at one that does not exist: where an item's directory sits is what tells a
    # bundled item from a record nothing on disk claims, so a fixture writing
    # its items outside both roots would be seeding the second while calling it
    # the first.
    monkeypatch.setattr(settings, "catalog_root", tmp_path / "catalog")
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")


def _write_house(catalog_dir: Path, house_id: str, domain: str, name: str, **meta) -> Path:
    house = catalog_dir / house_id
    (house / "steps").mkdir(parents=True, exist_ok=True)
    (house / "steps" / "01.py").write_text("result = 1\n")
    (house / "manifest.json").write_text(
        json.dumps(
            {
                "id": house_id,
                "name": name,
                "domain": domain,
                **meta,
                "steps": [{"index": 1, "instruction": "s1", "code": "steps/01.py"}],
            }
        )
    )
    return house


@pytest.fixture
def populated(tmp_path, _own_catalog_roots):
    """Three items in the catalog this machine loads at startup.

    Written under `settings.catalog_root`, where the bundled samples live, so
    they are bundled items by every check that asks — the roots are moved by the
    fixture above, not the layout inside them.
    """
    store = Store(db_path=tmp_path / "db.sqlite", artifacts_dir=tmp_path / "arts")
    ledger = Ledger(tmp_path / "catalog-ledger.json")

    async def go():
        await store.init()
        await load_house(
            store,
            ledger,
            _write_house(
                settings.domain_catalog_dir("house"),
                "zillow-1",
                "house",
                "Zillow One",
                category="bungalow",
                tags=["garage"],
                description="Cosy bungalow.",
            ),
        )
        await load_house(
            store,
            ledger,
            _write_house(
                settings.domain_catalog_dir("house"),
                "zillow-2",
                "house",
                "Zillow Two",
                category="two-storey",
            ),
        )
        await load_house(
            store,
            ledger,
            _write_house(
                settings.domain_catalog_dir("mechanical"), "part-1", "mechanical", "Flange"
            ),
        )

    asyncio.run(go())
    return store, ledger


@pytest.fixture
def with_received(populated):
    """The seeded catalog plus one item that arrived as a package.

    Received items land under the data directory rather than the bundled root,
    and that is the only thing that marks one as removable.
    """
    store, ledger = populated
    house = _write_house(imported_domain_dir("house"), "recv-1", "house", "Received One")
    asyncio.run(load_house(store, ledger, house))
    return store, ledger, house


def _received(store, ledger, house_id: str, name: str, provenance: dict | None) -> None:
    """One item under the received root, carrying the provenance it arrived with."""
    house = _write_house(imported_domain_dir("house"), house_id, "house", name)
    if provenance is not None:
        (house / "source.json").write_text(json.dumps(provenance))
    asyncio.run(load_house(store, ledger, house))


@pytest.fixture
def with_origins(populated, depot_origin):
    """The seeded catalog plus one item of each way an item can arrive here."""
    store, ledger = populated
    _received(
        store,
        ledger,
        "from-depot",
        "From The Depot",
        {
            "dataset": DEPOT_SENTENCE,
            "depot": {
                "catalog_id": DEPOT_CATALOG_ID,
                "version_id": None,
                "digest": DEPOT_DIGEST,
            },
        },
    )
    _received(
        store,
        ledger,
        "from-file",
        "From A File",
        {"dataset": "imported from the file a-colleague.cls"},
    )
    _received(store, ledger, "from-nowhere", "From Nowhere", None)
    return store


def _sources(client) -> dict[str, str | None]:
    body = client.get("/catalog", params={"limit": 200}).json()
    return {it["house_id"]: it["source"] for it in body["items"]}


def test_catalog_says_where_each_item_came_from(with_origins):
    """Removable already says an item arrived here. It does not say from where,
    and those are the two questions the panel needs to answer separately."""
    with TestClient(create_app(store=with_origins)) as c:
        source = _sources(c)

    assert source["from-depot"] == "depot"
    assert source["from-file"] == "file"
    # The other root holds the bundled samples and anything authored on this
    # machine, and this tool does not invent a rule for telling those apart.
    assert source["zillow-1"] == "local"
    assert source["part-1"] == "local"


def test_catalog_says_nothing_rather_than_guessing_when_an_item_did_not_say(with_origins):
    """Calling it a file would be a claim about where somebody's item came
    from, made on the strength of having failed to read it."""
    with TestClient(create_app(store=with_origins)) as c:
        assert _sources(c)["from-nowhere"] is None


def test_the_listing_leaves_the_origin_ids_to_the_route_that_answers_them(with_origins):
    """Nothing on this page needs them, and a public response is a poor place
    for a field to wait for a reader. What wants them asks the route below,
    which answers by `house_id` — so they can be joined back onto these items
    the day something does."""
    with TestClient(create_app(store=with_origins)) as c:
        item = next(
            it
            for it in c.get("/catalog", params={"limit": 200}).json()["items"]
            if it["house_id"] == "from-depot"
        )

    assert item["source"] == "depot"
    assert "depot_catalog_id" not in item
    assert "depot_digest" not in item


def test_catalog_filters_by_where_an_item_came_from(with_origins):
    with TestClient(create_app(store=with_origins)) as c:
        depot = c.get("/catalog", params={"source": "depot", "limit": 200}).json()
        local = c.get("/catalog", params={"source": "local", "limit": 200}).json()

    assert [it["house_id"] for it in depot["items"]] == ["from-depot"]
    assert depot["total"] == 1
    assert {it["house_id"] for it in local["items"]} == {"zillow-1", "zillow-2", "part-1"}


def test_catalog_facets_report_where_items_came_from(with_origins):
    """Counted like domains — over the whole catalog, so the chips do not
    renumber themselves as soon as one of them is clicked."""
    with TestClient(create_app(store=with_origins)) as c:
        body = c.get("/catalog").json()

    sources = {f["key"]: f for f in body["sources"]}
    assert sources["depot"]["count"] == 1
    assert sources["depot"]["label"] == "Depot"
    assert sources["file"]["count"] == 1
    assert sources["local"]["count"] == 3
    # Nothing is claimed about the one that did not say, so there is no chip
    # offering to gather those together under a name they never took.
    assert set(sources) == {"depot", "file", "local"}


def test_where_items_came_from_survives_a_ledger_nobody_can_read(with_origins, tmp_path):
    """Read off the items themselves rather than the ledger's copy of the same
    record, and this is why. A ledger that cannot be parsed already costs this
    page its tags, its categories and its thumbnails; it must not also cost
    every received item its origin, which is what a filter and a chip row are
    about to be built on.
    """
    with TestClient(create_app(store=with_origins)) as c:
        assert c.get("/catalog", params={"limit": 200}).json()["details_unavailable"] is False
        # Half of an entry: what a crash partway through a write leaves behind.
        (tmp_path / "catalog-ledger.json").write_text('{"zillow-1": {"step_c')

        body = c.get("/catalog", params={"limit": 200}).json()

    assert body["details_unavailable"] is True, "a stripped catalog must say so"
    source = {it["house_id"]: it["source"] for it in body["items"]}
    assert source["from-depot"] == "depot"
    assert source["from-file"] == "file"
    assert source["zillow-1"] == "local"
    assert [f["key"] for f in body["sources"]] == ["local", "depot", "file"]


def test_origins_names_only_what_can_be_matched_against_a_listing(with_origins):
    """A separate answer from the listing because the panel asking it needs
    every held catalogue at once, and a page of the catalog is not that."""
    with TestClient(create_app(store=with_origins)) as c:
        body = c.get("/catalog/origins/depot").json()

    assert body["items"] == [
        {
            "house_id": "from-depot",
            "catalog_id": DEPOT_CATALOG_ID,
            "version_id": None,
            "digest": DEPOT_DIGEST,
        }
    ]


def test_origins_leaves_out_an_item_with_no_catalogue_to_name(populated, depot_origin):
    """An older fetch that had nothing usable to record says only where it came
    from. It still reads as that origin, and there is still nothing to match a
    listing against."""
    store, ledger = populated
    _received(
        store,
        ledger,
        "from-depot-somehow",
        "From The Depot Somehow",
        {"dataset": DEPOT_SENTENCE},
    )

    with TestClient(create_app(store=store)) as c:
        assert _sources(c)["from-depot-somehow"] == "depot"
        assert c.get("/catalog/origins/depot").json()["items"] == []


def test_catalog_marks_only_received_items_removable(with_received):
    """A bundled item is a product asset on a read-only mount, and clearing one
    would last only until the next load walked that root again."""
    store, _, _ = with_received
    with TestClient(create_app(store=store)) as c:
        body = c.get("/catalog").json()

    removable = {it["house_id"]: it["removable"] for it in body["items"]}
    assert removable["recv-1"] is True
    assert removable["zillow-1"] is False and removable["part-1"] is False
    # Every one of them is here to be looked at, so none is the other case.
    assert not any(it["files_missing"] for it in body["items"])


def test_catalog_offers_to_remove_a_record_whose_files_are_gone(with_received):
    """The panel has to reach the one removal that is not about files at all.

    Nothing else in the response distinguishes this item: it lists under its
    name, it opens, and it clones, because the project and its versions are in
    the db. What it cannot do is be loaded again or be received again, and the
    listing is the only place the panel could be told so.
    """
    store, _, house = with_received
    shutil.rmtree(house)

    with TestClient(create_app(store=store)) as c:
        items = {it["house_id"]: it for it in c.get("/catalog").json()["items"]}

    assert items["recv-1"]["removable"] is True
    assert items["recv-1"]["files_missing"] is True
    # Saying local would be a claim about somebody's item made out of no longer
    # having it — this one arrived in a file, and the ledger still says so.
    assert items["recv-1"]["source"] is None
    assert items["zillow-1"]["files_missing"] is False
    assert items["zillow-1"]["source"] == "local"


def test_a_record_with_no_files_is_not_counted_among_the_local_ones(with_received):
    """The facet counts follow the same rule, or the chip would offer a filter
    that answers with an item it just miscounted."""
    store, _, house = with_received
    shutil.rmtree(house)

    with TestClient(create_app(store=store)) as c:
        body = c.get("/catalog").json()

    sources = {f["key"]: f["count"] for f in body["sources"]}
    assert sources == {"local": 3}


def test_removing_a_received_item_takes_it_out_of_the_catalog(with_received):
    store, ledger, house = with_received
    pid = asyncio.run(store.project_id_for_catalog_item("recv-1"))

    with TestClient(create_app(store=store)) as c:
        assert c.delete("/catalog/recv-1").status_code == 204
        listed = [it["house_id"] for it in c.get("/catalog").json()["items"]]

    assert "recv-1" not in listed and "zillow-1" in listed
    assert ledger.get("recv-1") is None  # not a stale entry left behind
    assert not house.exists()
    assert asyncio.run(store.get_project(pid)) is None


def test_removing_an_item_the_next_start_would_load_again_is_refused(with_received):
    store, ledger, _ = with_received
    with TestClient(create_app(store=store)) as c:
        r = c.delete("/catalog/zillow-1")
        listed = [it["house_id"] for it in c.get("/catalog").json()["items"]]

    assert r.status_code == 403
    # Names what makes the removal pointless — its files are in the root the
    # autoload walks — rather than saying it did not arrive as a package, which
    # is equally true of a record no directory claims and is cleared below.
    assert r.json()["detail"] == (
        "This catalog item's files are in the catalog this app loads at startup, "
        "so removing it here would not outlast the next start."
    )
    assert "zillow-1" in listed
    assert ledger.get("zillow-1") is not None


def test_removing_an_unknown_catalog_item_is_404(with_received):
    store, _, _ = with_received
    with TestClient(create_app(store=store)) as c:
        assert c.delete("/catalog/no-such-item").status_code == 404


def test_an_item_whose_files_are_gone_is_cleared_rather_than_refused(with_received):
    """The record outlives the files, and clearing it is the only thing that ends it.

    Nothing here put the item in this state — an edit outside the app did — but
    everything that could end it refuses: the project is read-only because it is
    a catalog item, and the removal above found no directory of ours to take.
    What is left is a row `load_house` cannot rebuild and an entry that refuses
    a fresh import of the same name.
    """
    store, ledger, house = with_received
    pid = asyncio.run(store.project_id_for_catalog_item("recv-1"))
    shutil.rmtree(house)

    with TestClient(create_app(store=store)) as c:
        r = c.delete("/catalog/recv-1")
        listed = [it["house_id"] for it in c.get("/catalog").json()["items"]]

    assert r.status_code == 204, r.text
    assert "recv-1" not in listed and "zillow-1" in listed
    assert ledger.get("recv-1") is None
    assert asyncio.run(store.get_project(pid)) is None


def test_an_unreadable_catalog_root_is_not_read_as_an_item_with_no_files(with_received):
    """Not having looked is not the same as having found nothing.

    The bundled items are sitting right there; the app just cannot get at the
    directory holding them. Clearing on the strength of that would take the
    project row and its versions of an item this route exists to refuse, and it
    would come back — as a new project id — only at the next start.
    """
    store, ledger, _ = with_received
    houses = settings.domain_catalog_dir("house")
    houses.chmod(0o000)
    try:
        with TestClient(create_app(store=store)) as c:
            listed = {it["house_id"]: it for it in c.get("/catalog").json()["items"]}
            r = c.delete("/catalog/zillow-1")
    finally:
        houses.chmod(0o755)

    assert r.status_code == 503, r.text
    assert "cannot tell" in r.json()["detail"]
    # Nothing was taken, and nothing offered: a Remove button here would promise
    # what the route has just refused.
    assert ledger.get("zillow-1") is not None
    assert asyncio.run(store.project_id_for_catalog_item("zillow-1")) is not None
    assert listed["zillow-1"]["files_missing"] is False
    assert listed["zillow-1"]["removable"] is False


def test_a_startup_catalog_that_is_not_there_is_not_read_as_one_holding_nothing(with_received):
    """The shape a lost mount takes: nothing fails, the root simply is not there.

    Every item loaded from it then answers to no directory, which is the same
    thing a record with no files answers — and this app ships that catalog, so
    the reading that costs nothing is the other one. A machine that never had a
    bundled catalog and one whose volume did not come back look identical here.
    """
    store, ledger, _ = with_received
    settings.catalog_root.rename(settings.catalog_root.with_name("elsewhere"))
    try:
        with TestClient(create_app(store=store)) as c:
            listed = {it["house_id"]: it for it in c.get("/catalog").json()["items"]}
            r = c.delete("/catalog/zillow-1")
    finally:
        settings.catalog_root.with_name("elsewhere").rename(settings.catalog_root)

    assert r.status_code == 503, r.text
    assert ledger.get("zillow-1") is not None
    assert asyncio.run(store.project_id_for_catalog_item("zillow-1")) is not None
    assert listed["zillow-1"]["files_missing"] is False
    assert listed["zillow-1"]["removable"] is False


def test_a_received_root_that_is_not_there_is_an_answer(with_received):
    """The other root, the other way round. It exists only once something has
    been received, and somebody taking all of it away is the case this route is
    asked for — so its absence says the files really are gone."""
    store, ledger, house = with_received
    shutil.rmtree(house.parent.parent)  # the whole received root

    with TestClient(create_app(store=store)) as c:
        r = c.delete("/catalog/recv-1")

    assert r.status_code == 204, r.text
    assert ledger.get("recv-1") is None


def test_an_unreadable_manifest_is_not_read_as_an_item_with_no_files(with_received):
    """The narrower way the same walk comes back short: the root lists, the
    directory lists, and one manifest will not open. That item claims no id and
    drops out of the walk exactly as a missing one does."""
    store, ledger, _ = with_received
    manifest = settings.domain_catalog_dir("house") / "zillow-1" / "manifest.json"
    manifest.chmod(0o000)
    try:
        with TestClient(create_app(store=store)) as c:
            r = c.delete("/catalog/zillow-1")
    finally:
        manifest.chmod(0o644)

    assert r.status_code == 503, r.text
    assert ledger.get("zillow-1") is not None


def test_an_unreadable_received_root_does_not_report_a_removal_that_took_nothing(with_received):
    """204 with the files still there would be the worst answer available: the
    user is told the copy is off this machine, and the next start loads it back."""
    store, _, house = with_received
    received_dir = house.parent
    received_dir.chmod(0o000)
    try:
        with TestClient(create_app(store=store)) as c:
            r = c.delete("/catalog/recv-1")
    finally:
        received_dir.chmod(0o755)

    assert r.status_code == 503, r.text
    assert house.exists()


def test_clearing_takes_a_directory_of_ours_left_standing_in_the_item_s_place(with_received):
    """A record with no files, and a directory an import would still refuse on.

    Its manifest is not json any more, so no id answers to it and every walk
    passes it by — but an import of the same package lands in that directory and
    refuses because something is already there. Clearing without taking it would
    answer "you can receive it again" and be wrong.
    """
    store, _, house = with_received
    (house / "source.json").write_text(json.dumps({"license": "MIT"}))
    payload = packed(house)
    (house / "manifest.json").write_text("{ not json")

    with TestClient(create_app(store=store)) as c:
        r = c.delete("/catalog/recv-1")
        again = c.post(
            "/packages/import",
            content=payload,
            headers={"Content-Type": "application/octet-stream"},
            params={"filename": "recv-1.cls"},
        )

    assert r.status_code == 204, r.text
    assert again.status_code == 200, again.text


def test_clearing_leaves_a_directory_that_answers_to_another_id_alone(with_received):
    """Ids fold onto directory names lossily, so a directory in one id's place can
    be another item. Only one claiming no id at all is this removal's to take."""
    store, ledger, house = with_received
    # The same directory recv-1 sat in, now holding an item of another name —
    # which is what a fold collision looks like on disk.
    (house / "manifest.json").write_text(
        json.dumps({"id": "other-item", "name": "Other", "domain": "house", "steps": []})
    )

    with TestClient(create_app(store=store)) as c:
        r = c.delete("/catalog/recv-1")

    assert r.status_code == 204, r.text
    assert house.exists(), "a directory claiming another id is not ours to take"
    assert ledger.get("recv-1") is None


def test_clearing_a_record_with_no_files_frees_the_name_for_the_same_package(with_received):
    """The point of clearing it: the package can be received here again.

    An import refuses an id the store already holds a project for, whether or
    not anything on disk is left of it — so while the record stood, the copy the
    sender still has could not be brought back.
    """
    store, _, house = with_received
    # The packer refuses an item that states no licence, and the sender's copy
    # is a package they published rather than the bare directory seeded here.
    (house / "source.json").write_text(json.dumps({"license": "MIT"}))
    payload = packed(house)
    shutil.rmtree(house)

    with TestClient(create_app(store=store)) as c:
        assert c.delete("/catalog/recv-1").status_code == 204
        again = c.post(
            "/packages/import",
            content=payload,
            headers={"Content-Type": "application/octet-stream"},
            params={"filename": "recv-1.cls"},
        )
        listed = [it["house_id"] for it in c.get("/catalog").json()["items"]]

    assert again.status_code == 200, again.text
    assert "recv-1" in listed


def test_no_route_reads_the_ledger_on_the_event_loop(with_received, monkeypatch):
    """Resolving the catalog ledger must never happen inline in a request.

    Several routes ask whether a project came from the catalog, and the answer
    lives in a JSON file beside the database. Read inline, that read runs on the
    loop that is answering every other request at the same moment — and a list
    response asks once per project, so it is not one read but many.
    ``asyncio.get_running_loop`` raises in a worker thread and returns in the
    loop's own, which is what tells the two apart without naming a thread.

    ``manifest.json`` is watched for the same reason: telling a received item
    from a bundled one means walking the catalog roots and reading what each
    directory declares, which is a pile of file reads per answer. It runs on the
    fixture that actually has a received item — with none, that walk returns
    before reading anything and the watch would pass over an inline read.
    """
    store, ledger, _ = with_received
    source_pid = asyncio.run(store.project_id_for_catalog_item("zillow-1"))
    watched = {"catalog-ledger.json", "manifest.json"}
    on_loop: list[str] = []
    real_read_text = Path.read_text

    def spy(self, *args, **kwargs):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass  # a worker thread — exactly where a file read belongs
        else:
            if self.name in watched:
                on_loop.append(str(self))
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", spy)
    with TestClient(create_app(store=store)) as c:
        # A clone is what makes a listing resolve provenance per row, on top of
        # the one lookup it does for the whole response.
        clone = c.post(f"/projects/{source_pid}/clone", json={})
        assert clone.status_code == 201, clone.text
        assert c.get("/catalog").status_code == 200
        assert c.get("/projects").status_code == 200
        assert c.get(f"/projects/{clone.json()['id']}").status_code == 200
        # Refused, but only after the walk that decides it — which is the read.
        assert c.delete("/catalog/zillow-1").status_code == 403
    assert on_loop == []


def test_catalog_grouped_by_domain(populated):
    store, _ = populated
    with TestClient(create_app(store=store)) as c:
        body = c.get("/catalog").json()
    groups = body["groups"]
    # house group comes first, then mechanical
    assert [g["domain"] for g in groups] == ["house", "mechanical"]
    house = groups[0]
    assert house["label"] == "House"
    assert [it["name"] for it in house["items"]] == ["Zillow One", "Zillow Two"]
    assert all(it["project_id"] > 0 and it["steps"] == 1 for it in house["items"])
    # current_version_id is populated so the UI can clone the item
    assert all(it["current_version_id"] is not None for it in house["items"])
    assert groups[1]["label"] == "Mechanical"


def test_catalog_excludes_deleted_projects(populated):
    store, ledger = populated
    pid = asyncio.run(store.project_id_for_catalog_item("zillow-1"))
    with TestClient(create_app(store=store)) as c:
        # Deleted from under the running app, which is the case the listing has
        # to survive. Doing it before startup would test something else: the
        # item's directory is in the root the autoload walks, so the next start
        # reads the missing row as "not loaded" and builds the item again.
        asyncio.run(store.delete_project(pid))
        body = c.get("/catalog").json()
    names = [it["name"] for g in body["groups"] for it in g["items"]]
    assert "Zillow One" not in names
    assert "Zillow Two" in names
    # the flat list and total agree with the grouped view
    flat = [it["name"] for it in body["items"]]
    assert "Zillow One" not in flat and body["total"] == len(flat)


# --------------------------------------------------------------------------- #
# discovery metadata on items (#21)
# --------------------------------------------------------------------------- #


def test_catalog_items_carry_discovery_metadata(populated):
    store, _ = populated
    with TestClient(create_app(store=store)) as c:
        body = c.get("/catalog").json()
    items = {it["house_id"]: it for it in body["items"]}
    z1 = items["zillow-1"]
    assert z1["category"] == "bungalow"
    assert z1["tags"] == ["garage"]
    assert z1["description"] == "Cosy bungalow."
    assert z1["thumbnail_url"] is None  # nothing baked for this fixture
    # legacy item without metadata: safe defaults
    flange = items["part-1"]
    assert flange["category"] is None
    assert flange["tags"] == [] and flange["description"] is None


def test_catalog_thumbnail_url_points_at_final_version(tmp_path):
    store = Store(db_path=tmp_path / "db.sqlite", artifacts_dir=tmp_path / "arts")
    ledger = Ledger(tmp_path / "catalog-ledger.json")
    house = _write_house(
        tmp_path / "cat", "h1", "house", "Casa", thumbnail="artifacts/thumbnail.png"
    )
    (house / "artifacts").mkdir(parents=True, exist_ok=True)
    (house / "artifacts" / "thumbnail.png").write_bytes(b"\x89PNG fake")

    async def go():
        await store.init()
        await load_house(store, ledger, house)

    asyncio.run(go())
    with TestClient(create_app(store=store)) as c:
        item = c.get("/catalog").json()["items"][0]
        assert item["thumbnail_url"] == (
            f"/versions/{item['current_version_id']}/artifacts/thumbnail"
        )
        # and the URL actually serves the PNG
        r = c.get(item["thumbnail_url"])
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/png"
        assert r.content == b"\x89PNG fake"


def test_catalog_thumbnail_survives_a_current_version_move(tmp_path):
    """The thumbnail is baked onto one specific version, so the URL has to name
    that version rather than whatever is current. Every path that moved a
    catalog item's current pointer used to 404 its thumbnail."""
    store = Store(db_path=tmp_path / "db.sqlite", artifacts_dir=tmp_path / "arts")
    ledger = Ledger(tmp_path / "catalog-ledger.json")
    house = _write_house(
        tmp_path / "cat", "h1", "house", "Casa", thumbnail="artifacts/thumbnail.png"
    )
    (house / "artifacts").mkdir(parents=True, exist_ok=True)
    (house / "artifacts" / "thumbnail.png").write_bytes(b"\x89PNG fake")

    async def go():
        await store.init()
        await load_house(store, ledger, house)
        pid = await store.project_id_for_catalog_item("h1")
        # A second version to move current onto — what revert/chat/generate did.
        moved = await store.add_version(pid, "v2", "result = 2", ok=True)
        await store.set_current_version(pid, moved.id)
        return moved.id

    moved_to = asyncio.run(go())

    with TestClient(create_app(store=store)) as c:
        item = c.get("/catalog").json()["items"][0]
        assert item["current_version_id"] == moved_to  # the move really happened
        r = c.get(item["thumbnail_url"])
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/png"
        assert r.content == b"\x89PNG fake"


# --------------------------------------------------------------------------- #
# search + filters + pagination over a 100+ entry ledger (#21 AC)
# --------------------------------------------------------------------------- #


@pytest.fixture
def big_catalog(tmp_path):
    """120 catalog items backed by real (empty) projects — loader bypassed for
    speed; search/filter/pagination read the ledger's metadata, not versions."""
    store = Store(db_path=tmp_path / "db.sqlite", artifacts_dir=tmp_path / "arts")
    ledger = Ledger(tmp_path / "catalog-ledger.json")

    async def go():
        await store.init()
        for i in range(120):
            domain = "house" if i % 2 == 0 else "mechanical"
            category = ("bungalow", "two-storey", "bracket")[i % 3]
            await store.create_project(f"Item {i:03d}", catalog_item_id=f"item-{i:03d}")
            ledger.record(
                f"item-{i:03d}",
                1,
                name=f"Item {i:03d}",
                domain=domain,
                category=category,
                tags=["garage"] if i % 5 == 0 else ["shaft"],
                description=f"Fixture number {i}.",
            )

    asyncio.run(go())
    return store


def test_catalog_search_by_name(big_catalog):
    with TestClient(create_app(store=big_catalog)) as c:
        body = c.get("/catalog", params={"q": "item 01"}).json()
    # Item 010..019 match "item 01" (case-insensitive substring)
    assert body["total"] == 10
    assert all("Item 01" in it["name"] for it in body["items"])


def test_catalog_search_matches_tags_and_description(big_catalog):
    with TestClient(create_app(store=big_catalog)) as c:
        by_tag = c.get("/catalog", params={"q": "garage"}).json()
        by_desc = c.get("/catalog", params={"q": "fixture number 7."}).json()
    assert by_tag["total"] == 24  # every 5th of 120
    assert all("garage" in it["tags"] for it in by_tag["items"])
    assert by_desc["total"] == 1
    assert by_desc["items"][0]["name"] == "Item 007"


def test_catalog_domain_and_category_filters(big_catalog):
    with TestClient(create_app(store=big_catalog)) as c:
        houses = c.get("/catalog", params={"domain": "house", "limit": 200}).json()
        bungalows = c.get(
            "/catalog", params={"domain": "house", "category": "bungalow", "limit": 200}
        ).json()
    assert houses["total"] == 60
    assert all(it["domain"] == "house" for it in houses["items"])
    # houses are the even i; bungalows are i % 3 == 0 -> i % 6 == 0 -> 20 items
    assert bungalows["total"] == 20
    assert all(it["category"] == "bungalow" for it in bungalows["items"])


def test_catalog_search_combines_with_filters(big_catalog):
    with TestClient(create_app(store=big_catalog)) as c:
        body = c.get("/catalog", params={"q": "garage", "domain": "house", "limit": 200}).json()
    # garage-tagged: i % 5 == 0; house: i % 2 == 0 -> i % 10 == 0 -> 12 items
    assert body["total"] == 12
    assert all(it["domain"] == "house" and "garage" in it["tags"] for it in body["items"])


def test_catalog_pagination_is_stable_and_complete(big_catalog):
    with TestClient(create_app(store=big_catalog)) as c:
        pages, seen = [], []
        for offset in range(0, 120, 50):
            page = c.get("/catalog", params={"limit": 50, "offset": offset}).json()
            assert page["total"] == 120
            assert page["limit"] == 50 and page["offset"] == offset
            pages.append(page)
            seen += [it["house_id"] for it in page["items"]]
    assert [len(p["items"]) for p in pages] == [50, 50, 20]
    assert len(seen) == len(set(seen)) == 120  # no overlap, nothing dropped


def test_catalog_facets_report_domains_and_categories(big_catalog):
    with TestClient(create_app(store=big_catalog)) as c:
        body = c.get("/catalog").json()
        scoped = c.get("/catalog", params={"domain": "mechanical"}).json()
    domains = {f["key"]: f for f in body["domains"]}
    assert domains["house"]["count"] == 60
    assert domains["house"]["label"] == "House"
    assert domains["mechanical"]["count"] == 60
    # facet counts stay global while categories follow the domain filter
    cats = {f["key"]: f["count"] for f in scoped["categories"]}
    assert sum(cats.values()) == 60
    assert {f["key"]: f for f in scoped["domains"]}["house"]["count"] == 60


def test_catalog_default_page_bounds_response(big_catalog):
    """No params still answers quickly and bounded — the UI's first paint."""
    with TestClient(create_app(store=big_catalog)) as c:
        body = c.get("/catalog").json()
    assert body["total"] == 120
    assert len(body["items"]) == body["limit"] <= 100
    # groups mirror the returned page only (never the whole catalog)
    grouped = [it["house_id"] for g in body["groups"] for it in g["items"]]
    assert grouped == [it["house_id"] for it in body["items"]]


def test_catalog_rejects_bad_pagination(big_catalog):
    with TestClient(create_app(store=big_catalog)) as c:
        assert c.get("/catalog", params={"limit": 0}).status_code == 422
        assert c.get("/catalog", params={"limit": 1000}).status_code == 422
        assert c.get("/catalog", params={"offset": -1}).status_code == 422


def test_the_domains_offered_are_the_registered_ones_not_the_stocked_ones(tmp_path):
    """A facet exists only where an item does, which is the right answer to
    "what is here" and the wrong one to "what can I look for". Narrowing a
    listing elsewhere by a domain nothing local uses is the whole case for
    this."""
    store = Store(db_path=tmp_path / "db.sqlite", artifacts_dir=tmp_path / "arts")
    with TestClient(create_app(store=store)) as client:
        listing = client.get("/catalog").json()
        offered = client.get("/catalog/domains").json()

    assert listing["domains"] == []  # nothing is stocked
    assert [d.key for d in all_domains()] == [d["key"] for d in offered["domains"]]
    assert [d.label for d in all_domains()] == [d["label"] for d in offered["domains"]]


def test_the_domains_route_does_not_shadow_the_listing(tmp_path):
    """Both hang off the same prefix, and one of them is reached by a path that
    could as easily have been read as an item id."""
    store = Store(db_path=tmp_path / "db.sqlite", artifacts_dir=tmp_path / "arts")
    with TestClient(create_app(store=store)) as client:
        listing = client.get("/catalog")

    assert listing.status_code == 200
    assert "items" in listing.json()


def test_a_cancelled_removal_keeps_the_gate_until_its_worker_is_done(with_received, monkeypatch):
    """Cancelling the request must not hand the gate to an import mid-removal.

    The removals go to worker threads, which cancelling does not reach, so a
    gate held by an `async with` in the request's own task is given up at the
    moment the await is interrupted — while directories are still being taken
    away. An import let in there decides a name is free, writes it, and has the
    removal that was already running carry off what it just put in place.

    The same shape as `place_package` on the import side, and asserted the same way it
    is described: the gate belongs to the work, so it is still held once the
    request has been cancelled and is free once the work is done.
    """
    store, _, _ = with_received
    reached = threading.Event()
    may_finish = threading.Event()
    unstaged_remove = catalog_router.remove_imported_house

    async def staged_remove(*args, **kwargs):
        reached.set()
        # A wait cancelling cannot reach, which is what every removal under this
        # gate really is.
        await asyncio.to_thread(may_finish.wait, 20)
        return await unstaged_remove(*args, **kwargs)

    monkeypatch.setattr(catalog_router, "remove_imported_house", staged_remove)

    async def go():
        request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(import_gate=asyncio.Lock()))
        )
        removing = asyncio.create_task(catalog_router.remove_catalog_item("recv-1", request, store))
        await asyncio.to_thread(reached.wait, 20)
        removing.cancel()
        # Give the cancellation every chance to land before asking.
        for _ in range(50):
            if removing.done():
                break
            await asyncio.sleep(0.01)
        gate = request.app.state.import_gate
        held_while_working = gate.locked()

        may_finish.set()
        with contextlib.suppress(asyncio.CancelledError):
            await removing
        return held_while_working, gate.locked()

    held_while_working, held_after = asyncio.run(go())

    assert held_while_working, "the gate was let go while the removal was still running"
    assert not held_after, "and it has to come back once the removal is over"
