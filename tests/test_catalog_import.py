"""Putting a received `.cls` into the catalog on this machine.

Imported items do not go where the bundled ones live. That directory ships with
the image and is mounted read-only in the deployment, which is right — it is a
product asset. What someone downloads is their own data, so it lands beside
their settings and their store.
"""

from __future__ import annotations

import asyncio
import errno
import io
import json
import logging
import os
import shutil
import zipfile
from pathlib import Path
from unittest import mock

import pytest

from cadless.catalog.importer import (
    CatalogImportConflict,
    CatalogImportError,
    CatalogImportUnavailable,
    import_package,
    imported_catalog_root,
    imported_domain_dir,
    imported_item_dir,
    received_origins,
    scan_startup_catalog,
    unclaimed_places_of,
)
from cadless.catalog.ledger import Ledger
from cadless.catalog.loader import load_house, remove_imported_house
from cadless.catalog.manifest import load_manifest
from cadless.catalog.pack import read_cls
from cadless.config import settings
from cadless.store import Store
from tests.cls_fixtures import packed

ALLOWED_STEP = "from build123d import Box\n\nresult = Box(10, 10, 10)\n"
BANNED_STEP = "import os\n\nresult = os.getcwd()\n"


@pytest.fixture(autouse=True)
def isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(settings, "catalog_root", tmp_path / "bundled")
    (tmp_path / "data").mkdir()


def write_item(root: Path, *, item_id: str = "l-bracket", step: str = ALLOWED_STEP) -> Path:
    item = root / item_id
    (item / "steps").mkdir(parents=True, exist_ok=True)
    (item / "artifacts" / "01").mkdir(parents=True, exist_ok=True)
    (item / "steps" / "01.py").write_text(step)
    (item / "artifacts" / "01" / "model.stl").write_bytes(b"solid\n")
    (item / "artifacts" / "thumbnail.png").write_bytes(b"\x89PNG\r\n")
    (item / "manifest.json").write_text(
        json.dumps(
            {
                "id": item_id,
                "name": "L-Bracket",
                "domain": "mechanical",
                "tags": ["bracket"],
                "thumbnail": "artifacts/thumbnail.png",
                "steps": [{"index": 1, "instruction": "A bracket.", "code": "steps/01.py"}],
            },
            indent=2,
        )
    )
    (item / "source.json").write_text(json.dumps({"license": "MIT", "author": "A Person"}))
    return item


def package(tmp_path, **kwargs):
    source = tmp_path / "authored"
    source.mkdir(exist_ok=True)
    item = write_item(source, **kwargs)
    return read_cls(packed(item, author="Johnny Pi"))


def test_an_imported_item_is_one_the_existing_loader_can_read(tmp_path):
    """The point of the whole exercise. If the catalog loader cannot read what
    was written, the item is on disk and invisible."""
    result = import_package(package(tmp_path), origin="https://api.example.test")

    manifest = load_manifest(result.item_dir)
    assert manifest.id == "l-bracket"
    assert manifest.steps[0].code == "steps/01.py"
    assert (result.item_dir / "steps" / "01.py").read_text() == ALLOWED_STEP
    assert (result.item_dir / "artifacts" / "01" / "model.stl").read_bytes() == b"solid\n"


def test_imported_items_land_beside_the_user_s_data_not_in_the_bundled_catalog(tmp_path):
    """The bundled catalog is mounted read-only in the shipped deployment and
    ships with the image. Writing there would fail in the container and would
    mix a download in with the product's own samples."""
    result = import_package(package(tmp_path), origin="a file")

    assert settings.data_dir in result.item_dir.parents
    assert settings.catalog_root not in result.item_dir.parents
    assert result.item_dir.parent == imported_domain_dir("mechanical")


def test_where_it_came_from_is_recorded_beside_it(tmp_path):
    """`source.json` is the provenance record the loader and the dedup index
    read. For an import the honest provenance is the import itself."""
    result = import_package(package(tmp_path), origin="https://api.example.test")

    source = json.loads((result.item_dir / "source.json").read_text())
    assert source["license"] == "MIT"
    assert source["author"] == "Johnny Pi"  # what the publisher confirmed at packing
    assert "https://api.example.test" in source["dataset"]
    assert result.package.canonical_digest in source["note"]


def test_the_handle_the_package_claims_is_recorded_too(tmp_path):
    """The stronger of the two claims, and the one worth keeping: a display name
    is not unique and the identity provider rewrites it, while a publisher
    refuses a package claiming a handle its uploader does not hold. Recording
    the name and dropping this would keep the weaker half.
    """
    source_dir = tmp_path / "authored"
    source_dir.mkdir()
    claimed = read_cls(packed(write_item(source_dir), author="Johnny Pi", author_handle="johnny"))

    result = import_package(claimed, origin="a file")

    source = json.loads((result.item_dir / "source.json").read_text())
    assert source["author_handle"] == "johnny"
    assert source["author"] == "Johnny Pi"


def test_a_package_packed_by_nobody_records_no_author(tmp_path):
    """Packed signed out — there is no confirmed identity to record, and
    inventing one would be worse than leaving it out."""
    source_dir = tmp_path / "authored"
    source_dir.mkdir()
    unsigned = read_cls(packed(write_item(source_dir)))

    result = import_package(unsigned, origin="a file")

    source = json.loads((result.item_dir / "source.json").read_text())
    assert "author" not in source
    assert "author_handle" not in source


def test_code_the_gate_refuses_is_never_written_to_disk(tmp_path):
    """This is the reason the import exists. Writing it first and checking
    later would leave the code sitting in the catalog, one load away from being
    run."""
    with pytest.raises(CatalogImportError) as raised:
        import_package(package(tmp_path, step=BANNED_STEP), origin="a file")

    assert "steps/01.py" in str(raised.value)
    assert not imported_domain_dir("mechanical").exists() or not list(
        imported_domain_dir("mechanical").iterdir()
    )


def test_an_item_that_would_take_over_an_existing_one_is_refused(tmp_path):
    """The catalog ledger keys on the manifest id, not the directory. An import
    sharing an id with something already here would replace it on the next load
    without anyone asking."""
    import_package(package(tmp_path), origin="first")

    with pytest.raises(CatalogImportError, match="l-bracket"):
        import_package(package(tmp_path), origin="second")


def test_an_id_already_used_by_a_bundled_item_is_refused_too(tmp_path):
    """Same hazard from the other direction — the bundled samples are items too."""
    bundled = settings.catalog_root / "mech-catalog"
    bundled.mkdir(parents=True)
    write_item(bundled)

    with pytest.raises(CatalogImportError, match="l-bracket"):
        import_package(package(tmp_path), origin="a file")


def _seed(tmp_path):
    """A store and ledger positioned the way `catalog_state.ledger_for` expects."""
    store = Store(db_path=tmp_path / "db.sqlite", artifacts_dir=tmp_path / "artifacts")
    return store, Ledger(tmp_path / "catalog-ledger.json")


def test_a_received_item_is_found_by_the_id_it_declares(tmp_path):
    """Removing one asks "is it ours" and "where is it" at the same time, and
    which root the directory sits under answers both."""
    result = import_package(package(tmp_path), origin="a file")

    assert imported_item_dir("l-bracket") == result.item_dir
    assert set(received_origins()) == {"l-bracket"}


def test_a_bundled_item_is_not_one_of_ours(tmp_path):
    """A bundled item ships with the image on a read-only mount, and removing it
    would last only until the next load walked that root again."""
    bundled = settings.catalog_root / "mech-catalog"
    bundled.mkdir(parents=True)
    write_item(bundled)

    assert imported_item_dir("l-bracket") is None
    assert received_origins() == {}


def test_the_startup_catalog_names_the_ids_it_would_load_again(tmp_path):
    """`imported_item_dir` answering None is two different facts folded together.

    A bundled item and an id no directory anywhere claims both come back None,
    and only the first of them is a reason to refuse a removal: clearing it
    would last until the next start walked that root. This is the other half of
    that question, so the two can be told apart.
    """
    bundled = settings.catalog_root / "mech-catalog"
    bundled.mkdir(parents=True)
    write_item(bundled)
    write_item(imported_domain_dir("mechanical"), item_id="received-1")

    scan = scan_startup_catalog()

    assert scan.ids == frozenset({"l-bracket"})
    assert scan.claims("l-bracket") and not scan.claims("received-1")
    assert scan.complete


def test_a_missing_startup_catalog_names_nothing_rather_than_failing(tmp_path):
    """A machine holding only received items has no bundled root at all. That is
    an answer — nothing is here — rather than a walk that failed to look."""
    assert not settings.catalog_root.exists()

    scan = scan_startup_catalog()

    assert scan.ids == frozenset()
    assert scan.complete


def test_a_root_that_cannot_be_read_says_so_instead_of_answering_empty(tmp_path):
    """The distinction the callers act on: an id absent from this walk has been
    shown to be absent only if the walk read what it walked. A root, a domain
    directory and a manifest each fail differently and all three end the same
    way — an item missing from `ids` — so all three have to say so."""
    bundled = settings.catalog_root / "mech-catalog"
    bundled.mkdir(parents=True)
    write_item(bundled)
    write_item(bundled, item_id="second")

    (bundled / "l-bracket" / "manifest.json").chmod(0o000)
    try:
        unreadable_manifest = scan_startup_catalog()
    finally:
        (bundled / "l-bracket" / "manifest.json").chmod(0o644)

    bundled.chmod(0o000)
    try:
        unreadable_domain = scan_startup_catalog()
    finally:
        bundled.chmod(0o755)

    settings.catalog_root.chmod(0o000)
    try:
        unreadable_root = scan_startup_catalog()
    finally:
        settings.catalog_root.chmod(0o755)

    assert unreadable_manifest.ids == frozenset({"second"})
    assert not unreadable_manifest.complete
    assert not unreadable_domain.complete
    assert not unreadable_root.complete


def test_a_manifest_that_is_not_json_claims_no_id_and_is_not_a_failure(tmp_path):
    """Nobody can read it, which is a fact about the file rather than about this
    attempt at it — so the id it used to claim really is not claimed by anything,
    and a caller may act on that."""
    bundled = settings.catalog_root / "mech-catalog"
    bundled.mkdir(parents=True)
    write_item(bundled)
    (bundled / "l-bracket" / "manifest.json").write_text("{ not json")

    scan = scan_startup_catalog()

    assert scan.ids == frozenset()
    assert scan.complete


def test_a_directory_holding_an_id_s_place_and_claiming_nothing_is_found(tmp_path):
    """What an import would still refuse on after a record is cleared.

    An item lands in a directory named by a fold of its id, and a directory there
    blocks the import whether or not anything can read what is inside it.
    """
    write_item(imported_domain_dir("mechanical"), item_id="l-bracket")
    (imported_domain_dir("mechanical") / "l-bracket" / "manifest.json").write_text("{ no")

    assert unclaimed_places_of("l-bracket") == [imported_domain_dir("mechanical") / "l-bracket"]
    assert unclaimed_places_of("l bracket") == [imported_domain_dir("mechanical") / "l-bracket"]
    assert unclaimed_places_of("something-else") == []


def test_a_directory_that_claims_another_id_is_not_reported_as_a_free_place(tmp_path):
    """Ids fold onto directory names lossily, so the directory in one id's place
    can be another item — which is not the caller's to take away."""
    write_item(imported_domain_dir("mechanical"), item_id="l-bracket")

    assert unclaimed_places_of("l-bracket") == []


def test_every_domain_holding_the_place_is_reported_not_the_first(tmp_path):
    """A folded name can be free in one domain's directory and taken in another,
    and the import lands in the one the package names. Reporting only the first
    would leave the other standing, with the promise still false for it."""
    for domain in ("mechanical", "house"):
        place = imported_domain_dir(domain) / "l-bracket"
        place.mkdir(parents=True)
        (place / "manifest.json").write_text("{ not json")

    assert unclaimed_places_of("l-bracket") == [
        imported_domain_dir("house") / "l-bracket",
        imported_domain_dir("mechanical") / "l-bracket",
    ]


def test_a_place_whose_manifest_cannot_be_read_is_not_reported_as_claiming_nothing(tmp_path):
    """What this answers gets deleted, so "claims no id" has to mean the file
    said so — not that it would not open."""
    place = imported_domain_dir("mechanical") / "l-bracket"
    place.mkdir(parents=True)
    (place / "manifest.json").write_text('{"id": "l-bracket"}')
    (place / "manifest.json").chmod(0o000)
    try:
        assert unclaimed_places_of("l-bracket") == []
    finally:
        (place / "manifest.json").chmod(0o644)


def test_a_linked_place_is_left_alone(tmp_path):
    """The containment check beside the deletion, from the direction that would
    reach outside the root: what a link points at is not ours to take."""
    outside = tmp_path / "somebody-elses"
    outside.mkdir()
    (outside / "manifest.json").write_text("{ not json")
    imported_domain_dir("mechanical").mkdir(parents=True)
    (imported_domain_dir("mechanical") / "l-bracket").symlink_to(outside)

    assert unclaimed_places_of("l-bracket") == []
    assert (outside / "manifest.json").exists()


# --- What a received item says about where it came from --------------------
#
# Which root a directory sits under answers "can this be removed", and it used
# to be asked to answer "where did this come from" as well. It cannot: the same
# root holds packages fetched from somewhere and packages handed over on a
# drive, and the other root holds items authored on this machine alongside the
# bundled samples. What each item records about itself is what tells them apart.
#
# Only the answers this engine reaches on its own are here. Recognising an
# arrival some *other* build implements is that build's own reader, and the
# tests for one live with it. `tests/test_catalog_origins.py` covers the
# registry itself, and what an unregistered arrival reads as.


def test_an_arrival_cannot_record_a_key_the_import_witnessed(tmp_path):
    """The provenance record has to stay what this side actually saw.

    Refused before anything is unpacked, and as a stated refusal rather than an
    unnamed failure: the caller is a build with a bug in it, and finding out at
    the end of a long operation makes it a 500 where the same information would
    have been a 400 at the start.
    """
    with pytest.raises(CatalogImportError, match="must stay what the import witnessed"):
        import_package(
            package(tmp_path), origin="the depot", recorded={"license": "not yours to say"}
        )
    # And nothing was written on the way to refusing.
    assert received_origins() == {}


def test_a_package_handed_over_directly_reads_as_a_file(tmp_path):
    """It came through no listing, so there is none it belongs to and nothing
    to offer to check for updates."""
    import_package(package(tmp_path), origin="the file from-a-colleague.cls")

    assert received_origins()["l-bracket"].kind == "file"


def test_an_item_whose_provenance_cannot_be_read_is_not_called_anything(tmp_path):
    """ "We cannot tell" and "handed over directly" are different answers, and
    the quiet one is a claim about where somebody's item came from."""
    result = import_package(package(tmp_path), origin="the file from-a-colleague.cls")
    (result.item_dir / "source.json").unlink()

    origin = received_origins()["l-bracket"]

    assert origin.kind == "unknown"
    assert origin.catalog_id is None


def test_an_item_that_never_arrived_here_is_not_in_the_walk_at_all(tmp_path):
    """Bundled samples and work authored on this machine share a root, and this
    tool does not invent a rule for telling those two apart. Both are simply not
    among the things that arrived here."""
    bundled = settings.catalog_root / "mech-catalog"
    bundled.mkdir(parents=True)
    write_item(bundled)

    assert received_origins() == {}


def test_removing_a_received_item_takes_its_project_entry_and_files(tmp_path):
    """All three, or the item is only half gone: the project is what the user
    sees, the ledger entry is what makes the next load skip it, and the files
    are what the next import would collide with."""
    store, ledger = _seed(tmp_path)
    result = import_package(package(tmp_path), origin="a file")

    async def go():
        await store.init()
        pid = await load_house(store, ledger, result.item_dir)
        return pid, await remove_imported_house(store, ledger, "l-bracket")

    pid, removed = asyncio.run(go())

    assert removed is True
    assert asyncio.run(store.get_project(pid)) is None
    assert ledger.get("l-bracket") is None
    assert not result.item_dir.exists()


def test_removing_a_bundled_item_is_refused_and_leaves_it_alone(tmp_path):
    store, ledger = _seed(tmp_path)
    bundled = settings.catalog_root / "mech-catalog"
    bundled.mkdir(parents=True)
    item = write_item(bundled)

    async def go():
        await store.init()
        pid = await load_house(store, ledger, item)
        return pid, await remove_imported_house(store, ledger, "l-bracket")

    pid, removed = asyncio.run(go())

    assert removed is False
    assert item.exists()
    assert ledger.get("l-bracket") is not None
    assert asyncio.run(store.get_project(pid)) is not None


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a 0o000 directory anyway")
def test_one_unreadable_directory_does_not_take_the_listing_down(tmp_path):
    """`load_all` lets an item it cannot read cost only itself. This walk answers
    `GET /catalog` now, so one unreadable directory must not take the whole
    listing with it."""
    good = import_package(package(tmp_path), origin="a file")
    blocked = imported_catalog_root() / "house-catalog"
    blocked.mkdir(parents=True)
    blocked.chmod(0o000)
    try:
        assert set(received_origins()) == {"l-bracket"}
        assert imported_item_dir("l-bracket") == good.item_dir
    finally:
        blocked.chmod(0o755)  # so tmp_path can be cleaned up


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a 0o000 directory anyway")
def test_an_unreadable_received_root_reads_as_empty_rather_than_failing(tmp_path):
    """The same rule one level up. Nothing can be marked removable while the root
    cannot be listed, but the catalog still answers."""
    import_package(package(tmp_path), origin="a file")
    root = imported_catalog_root()
    root.chmod(0o000)
    try:
        assert received_origins() == {}
        assert imported_item_dir("l-bracket") is None
    finally:
        root.chmod(0o755)  # so tmp_path can be cleaned up


def test_a_directory_linking_out_of_the_root_is_not_an_item_here(tmp_path):
    """Nothing can put a symlink under the received root today — entries are
    written with `write_bytes` rather than extracted — but that guarantee is made
    in the import path, and this is the code holding the `rmtree`. It checks for
    itself rather than inheriting an invariant from another module.
    """
    store, ledger = _seed(tmp_path)
    outside = write_item(tmp_path / "elsewhere")
    domain_dir = imported_domain_dir("mechanical")
    domain_dir.mkdir(parents=True)
    (domain_dir / "l-bracket").symlink_to(outside, target_is_directory=True)

    assert imported_item_dir("l-bracket") is None
    assert received_origins() == {}
    assert asyncio.run(remove_imported_house(store, ledger, "l-bracket")) is False
    assert (outside / "manifest.json").exists()  # nothing outside the root was touched


def test_a_linked_bundled_item_still_blocks_an_import_of_its_id(tmp_path):
    """The containment check belongs to removal, not to "what is already here".

    A linked directory is still an item a package would collide with. Skipping
    it while looking for takeovers would let an import replace the bundled
    sample the link points at — the exact thing that check exists to stop.
    """
    target = write_item(tmp_path / "elsewhere")
    bundled = settings.catalog_root / "mech-catalog"
    bundled.mkdir(parents=True)
    (bundled / "l-bracket").symlink_to(target, target_is_directory=True)

    with pytest.raises(CatalogImportError, match="l-bracket"):
        import_package(package(tmp_path), origin="a file")


def test_a_removal_that_cannot_delete_the_files_still_frees_the_id(tmp_path):
    """Deleting a tree is not one step, and stopping partway through it used to
    be the worst outcome available: the manifest gone so nothing lists or
    reloads the item, the directory still there so no import may take the name.
    Removal moves the directory out of the scanned root first, the way an import
    moves one in, so what is left behind afterwards is inert.
    """
    store, ledger = _seed(tmp_path)
    first = import_package(package(tmp_path), origin="first")

    async def go():
        await store.init()
        await load_house(store, ledger, first.item_dir)
        return await remove_imported_house(store, ledger, "l-bracket")

    # Fail only the staging delete — the store's own artifact cleanup uses the
    # same call, and breaking that would be testing a different thing.
    real_rmtree = shutil.rmtree

    def fail_in_staging(path, *args, **kwargs):
        if ".importing" in str(path):
            raise OSError("cannot delete the tree")
        return real_rmtree(path, *args, **kwargs)

    with mock.patch("shutil.rmtree", side_effect=fail_in_staging):
        assert asyncio.run(go()) is True

    assert not first.item_dir.exists()
    assert imported_item_dir("l-bracket") is None
    again = import_package(package(tmp_path), origin="second")
    assert again.item_dir.exists()


def test_the_same_item_can_be_received_again_once_it_is_removed(tmp_path):
    """The dead end this closes. Deleting the project on its own left the ledger
    entry and the files behind, and `import_package` refuses an id either one
    still answers to — so the item could be neither restored nor received again.
    """
    store, ledger = _seed(tmp_path)
    first = import_package(package(tmp_path), origin="first")

    async def go():
        await store.init()
        await load_house(store, ledger, first.item_dir)
        return await remove_imported_house(store, ledger, "l-bracket")

    assert asyncio.run(go()) is True

    again = import_package(package(tmp_path), origin="second")
    assert again.item_dir.exists()
    assert imported_item_dir("l-bracket") == again.item_dir


def test_a_catalog_that_cannot_be_written_says_so_before_anything_is_unpacked(tmp_path):
    """The deployment mounts a catalog read-only, and an import that discovers
    that halfway through leaves a part-written directory behind."""
    (tmp_path / "data").chmod(0o555)
    try:
        with pytest.raises(CatalogImportError, match="writable"):
            import_package(package(tmp_path), origin="a file")
    finally:
        (tmp_path / "data").chmod(0o755)


def test_a_write_that_gives_way_is_logged_in_full_and_reported_in_brief(
    tmp_path, monkeypatch, caplog
):
    """Which path failed is what the operator needs and what the caller cannot use.

    Injected rather than provoked: every name a package may carry is now one a
    filesystem can hold, which is what the reader's rules are for. What is left
    for this handler is the machine itself giving way — a full disk, a mount
    withdrawn — and the endpoint hands whatever it says to anyone who asks.
    """
    payload = package(tmp_path)

    def gives_way(self, data):
        raise OSError(errno.ENOSPC, "No space left on device", str(self))

    monkeypatch.setattr(Path, "write_bytes", gives_way)

    with caplog.at_level(logging.ERROR, logger="cadless.catalog.importer"):
        with pytest.raises(CatalogImportUnavailable) as raised:
            import_package(payload, origin="a file")

    reported = str(raised.value)
    assert str(tmp_path) not in reported, reported
    assert "Errno" not in reported, reported
    assert any(str(tmp_path) in record.getMessage() for record in caplog.records), caplog.text


def test_the_takeover_report_names_the_item_and_not_where_it_lives(tmp_path):
    """The last of the three checks: an item found by reading what is on disk,
    because its directory no longer folds from its id.

    Which directory that turned out to be is this machine's business. The
    caller is told which item they would take over, which is the part of it
    they can do anything about.
    """
    first = import_package(carried(a_manifest()), origin="a file")
    first.item_dir.rename(first.item_dir.parent / "renamed-by-hand")

    with pytest.raises(CatalogImportConflict, match="l-bracket") as raised:
        import_package(carried(a_manifest()), origin="a file")

    reported = str(raised.value)
    assert str(tmp_path) not in reported, reported
    assert "renamed-by-hand" not in reported, reported


def test_nothing_half_written_is_left_where_the_catalog_looks(tmp_path):
    """`discover_houses` treats any directory holding a manifest as an item, so
    the manifest is written last and the whole item is moved into place at once.
    """
    result = import_package(package(tmp_path), origin="a file")

    siblings = list(result.item_dir.parent.iterdir())
    assert siblings == [result.item_dir]
    assert not [path for path in siblings if path.name.startswith(".")]


# The manifest the package carries is the part no packer produces a bad version
# of, so these build it by hand. Everything above arrives from an item
# this side wrote; a manifest written by whoever sent the package does not have
# to agree with anything in it, and it is the document the loader obeys.


def carried(manifest: dict, entries: dict[str, bytes] | None = None):
    """A package carrying exactly this manifest, and exactly these entries."""
    members = dict(entries or {"steps/01.py": ALLOWED_STEP.encode()})
    members["cls.json"] = json.dumps(
        {
            "format_version": 1,
            "content_version": "1.0.0",
            "license": "MIT",
            "title": "L-Bracket",
            "included_fields": ["artifacts", "steps"],
            "cadless_manifest": manifest,
        }
    ).encode()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as out:
        for name, blob in members.items():
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            out.writestr(info, blob)
    return read_cls(buffer.getvalue())


def a_manifest(**patch) -> dict:
    base = {
        "id": "l-bracket",
        "name": "L-Bracket",
        "domain": "mechanical",
        "steps": [{"index": 1, "instruction": "A bracket.", "code": "steps/01.py"}],
    }
    base.update(patch)
    return base


def nothing_imported() -> bool:
    root = imported_domain_dir("mechanical")
    return not root.exists() or not list(root.iterdir())


def test_a_step_whose_code_the_gate_never_read_is_refused(tmp_path):
    """The gate reads `steps/**`. The loader reads whatever the manifest names.

    A manifest pointing its step at an entry outside `steps/` puts code on this
    machine that passed nothing — and the import reports a package whose steps
    were all checked, because the file that became the step was never one of
    them. The packer already refuses to build this; the reader has to as well.
    """
    package = carried(
        a_manifest(steps=[{"index": 1, "instruction": "A bracket.", "code": "artifacts/pwn.py"}]),
        {"steps/01.py": ALLOWED_STEP.encode(), "artifacts/pwn.py": BANNED_STEP.encode()},
    )

    with pytest.raises(CatalogImportError):
        import_package(package, origin="a file")

    assert nothing_imported()


def test_a_step_naming_a_file_the_package_does_not_carry_is_refused(tmp_path):
    with pytest.raises(CatalogImportError):
        import_package(
            carried(
                a_manifest(
                    steps=[{"index": 1, "instruction": "A bracket.", "code": "steps/missing.py"}]
                )
            ),
            origin="a file",
        )

    assert nothing_imported()


def test_a_thumbnail_pointing_out_of_the_item_is_refused(tmp_path):
    with pytest.raises(CatalogImportError):
        import_package(carried(a_manifest(thumbnail="../../settings.json")), origin="a file")

    assert nothing_imported()


def test_a_step_artifact_pointing_out_of_the_item_is_refused(tmp_path):
    """`load_house` copies these into the store and the API serves them.

    `settings.json` sits two directories up from an imported item and holds
    every provider key.
    """
    package = carried(
        a_manifest(
            steps=[
                {
                    "index": 1,
                    "instruction": "A bracket.",
                    "code": "steps/01.py",
                    "artifacts": {"stl": "../../settings.json"},
                }
            ]
        )
    )

    with pytest.raises(CatalogImportError):
        import_package(package, origin="a file")

    assert nothing_imported()


def test_a_manifest_the_loader_could_not_read_is_refused_before_anything_is_written(tmp_path):
    """Refused as a bad package, not discovered as a failure after the write.

    An item written and then found unreadable holds its id against every later
    attempt, and `discover_houses` counts it — so it is not only its own loss.
    """
    with pytest.raises(CatalogImportError):
        import_package(carried({"id": "no-steps", "name": "Broken"}), origin="a file")

    assert nothing_imported()


def test_an_artifact_kind_that_is_not_a_name_is_refused(tmp_path):
    """The kind is not a label — the loader spells a filename out of it.

    `load_house` copies each artifact to `model.{kind}`, so a kind carrying a
    separator, a control character or three hundred characters is a write that
    fails partway through loading an item already on disk. The id stays taken,
    and every restart tries the same item again and leaves another project
    behind, none of them in the ledger to clear.
    """
    for kind in ("a/b", "x" * 300, "a\x00b", ""):
        with pytest.raises(CatalogImportError):
            import_package(
                carried(
                    a_manifest(
                        steps=[
                            {
                                "index": 1,
                                "instruction": "A bracket.",
                                "code": "steps/01.py",
                                "artifacts": {kind: "steps/01.py"},
                            }
                        ]
                    )
                ),
                origin="a file",
            )
        assert nothing_imported()


def test_steps_that_do_not_start_at_one_are_refused(tmp_path):
    package = carried(
        a_manifest(
            steps=[
                {"index": 2, "instruction": "A bracket.", "code": "steps/01.py"},
                {"index": 3, "instruction": "More.", "code": "steps/01.py"},
            ]
        )
    )

    with pytest.raises(CatalogImportError):
        import_package(package, origin="a file")

    assert nothing_imported()


def test_an_id_too_long_to_name_a_directory_is_refused(tmp_path):
    with pytest.raises(CatalogImportError):
        import_package(carried(a_manifest(id="l" * 400)), origin="a file")

    assert nothing_imported()


def test_an_id_of_nothing_but_space_is_refused(tmp_path):
    with pytest.raises(CatalogImportError):
        import_package(carried(a_manifest(id="   ")), origin="a file")

    assert nothing_imported()


def test_a_domain_this_tool_does_not_know_is_refused(tmp_path):
    with pytest.raises(CatalogImportError):
        import_package(carried(a_manifest(domain="not-a-domain")), origin="a file")

    assert nothing_imported()


def test_an_id_already_loaded_here_is_refused_even_with_nothing_on_disk(tmp_path):
    """The two records of "taken" do not agree, and the ledger is the one that
    decides a takeover.

    An entry outlives its directory whenever a sample is dropped from a newer
    image or a directory is removed without clearing it. `load_house` then finds
    the entry, sees content that does not match, and clears the project it
    pointed at — so an import that only checked the disk would delete somebody's
    project and install itself under its id.
    """
    with pytest.raises(CatalogImportError, match="l-bracket"):
        import_package(carried(a_manifest()), origin="a file", already_loaded={"l-bracket"})

    assert nothing_imported()


def test_a_manifest_that_would_not_survive_being_written_is_refused(tmp_path):
    """Checked as received is not the same as checked as written.

    `NaN` is a float the model accepts and JSON serialisation turns into `null`,
    which `bbox` has no room for — so the item the loader reads back is one it
    refuses, by which time the write has happened, the id is taken, and nothing
    in the app can give it back.
    """
    package = carried(
        a_manifest(
            steps=[
                {
                    "index": 1,
                    "instruction": "A bracket.",
                    "code": "steps/01.py",
                    "geometry": {"volume": 1.0, "bbox": [float("nan"), 1.0, 2.0]},
                }
            ]
        )
    )

    with pytest.raises(CatalogImportError):
        import_package(package, origin="a file", already_loaded=())

    assert nothing_imported()


def test_a_manifest_that_cannot_be_written_at_all_is_refused(tmp_path):
    """A lone surrogate is a string the model accepts and UTF-8 cannot encode.

    Left to the write, it comes back as neither a refusal nor a failure this
    route knows how to describe — the caller gets an internal error for a file
    that was simply not one we can take.
    """
    with pytest.raises(CatalogImportError):
        import_package(carried(a_manifest(name="N\ud800")), origin="a file", already_loaded=())

    assert nothing_imported()


def test_what_is_written_is_the_manifest_as_checked(tmp_path):
    """Not as received. A key this version does not know is a key it did not
    check, and writing it leaves the loader obeying a document nobody read."""
    result = import_package(
        carried(a_manifest(unknown_future_key={"run": "anything"})), origin="a file"
    )

    written = json.loads((result.item_dir / "manifest.json").read_text())
    assert "unknown_future_key" not in written, written


def test_entries_the_digest_does_not_cover_are_not_written(tmp_path):
    """`checksums` and `signature` are outside the digest by design.

    The panel tells someone their package matches the fingerprint they were
    given. Writing files that fingerprint never covered puts them in the item
    under that same sentence — a claim slightly larger than what was checked,
    which is the one thing this path must not do.
    """
    result = import_package(
        carried(a_manifest(), {"steps/01.py": ALLOWED_STEP.encode(), "checksums": b"whatever\n"}),
        origin="a file",
    )

    assert not (result.item_dir / "checksums").exists()
    assert (result.item_dir / "steps" / "01.py").exists()


def test_two_ids_that_name_one_directory_do_not_replace_each_other(tmp_path):
    """The id check and the directory name do not agree on what "taken" means.

    The directory is a lossy fold of the id, so two ids nothing else considers
    equal land in one place — and the second import would delete the first
    item's files while its ledger entry went on pointing at them.
    """
    first = import_package(carried(a_manifest(id="my-part")), origin="first")

    with pytest.raises(CatalogImportError):
        import_package(carried(a_manifest(id="My Part")), origin="second")

    assert json.loads((first.item_dir / "manifest.json").read_text())["id"] == "my-part"


def test_a_package_that_names_where_its_line_began_carries_that_forward(tmp_path):
    """`derived_from` is the one claim a package makes about a listing other than
    the one it arrived through, and only the package can carry it.

    Covered directly because the tests that covered it went with the route that
    used to produce such packages. The path is still live: `_provenance` reads
    the claim out of `cls.json` on every import, whoever wrote the package.
    """
    source_dir = tmp_path / "authored"
    source_dir.mkdir(exist_ok=True)
    item = write_item(source_dir)
    (item / "source.json").write_text(
        json.dumps(
            {
                "license": "MIT",
                "derived_from": {
                    "catalog_id": "3f2a" * 8,
                    "version_id": "v-1",
                    "digest": "5e0a" * 16,
                    "unchanged": True,
                },
            }
        )
    )

    result = import_package(read_cls(packed(item)), origin="a file")

    carried = json.loads((result.item_dir / "source.json").read_text())["derived_from"]
    assert carried == {
        "catalog_id": "3f2a" * 8,
        "version_id": "v-1",
        "digest": "5e0a" * 16,
        "unchanged": True,
    }


def test_a_derivation_naming_no_catalogue_is_dropped_rather_than_written_empty(tmp_path):
    """A reference with nothing to look up is not a reference. Written empty it
    would read as "this came from a listing" to anything that checks the key."""
    source_dir = tmp_path / "authored"
    source_dir.mkdir(exist_ok=True)
    item = write_item(source_dir)
    (item / "source.json").write_text(
        json.dumps({"license": "MIT", "derived_from": {"version_id": "v-1"}})
    )

    result = import_package(read_cls(packed(item)), origin="a file")

    assert "derived_from" not in json.loads((result.item_dir / "source.json").read_text())
