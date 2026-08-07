"""Taking a received `.cls` into the catalog on this machine, through the app.

A package that arrived without passing an upload gate was never checked by
anyone, and the code it carries runs here. So the endpoint is
held to refusing *before* it writes: a digest that does not match what the
sender published, code the gate rejects, an id something here already answers
to, or a body larger than a package may be.

Nothing here signs in. Importing a file is nobody's account operation — a
package handed over on a USB stick passed no gate anywhere, and that is
precisely the case with no other check in front of it.
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import io
import itertools
import json
import shutil
import tempfile
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.routers import packages as packages_router
from cadless.catalog import importer as catalog_importer
from cadless.catalog.importer import imported_catalog_root
from cadless.catalog.pack import MAX_UNCOMPRESSED_BYTES, digest_of
from cadless.config import settings
from cadless.store import Store
from tests.cls_fixtures import packed
from tests.zip_records import rewritten

STEP_SOURCE = "from build123d import Box\n\nresult = Box(10, 10, 10)\n"
BANNED_SOURCE = "import os\n\nresult = os.getcwd()\n"


@pytest.fixture(autouse=True)
def isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    (tmp_path / "data").mkdir()
    monkeypatch.setattr(settings, "catalog_root", tmp_path / "catalog")


@pytest.fixture
def client(tmp_path):
    store = Store(db_path=tmp_path / "db.sqlite", artifacts_dir=tmp_path / "artifacts")
    with TestClient(create_app(store=store)) as c:
        yield c


def write_item(root: Path, name: str = "l-bracket", *, step_source: str = STEP_SOURCE) -> Path:
    """One mechanical catalog item, wherever it is asked for."""
    item = root / name
    (item / "steps").mkdir(parents=True)
    (item / "artifacts" / "01").mkdir(parents=True)
    (item / "steps" / "01.py").write_text(step_source)
    (item / "artifacts" / "01" / "model.stl").write_bytes(b"solid\n")
    (item / "manifest.json").write_text(
        json.dumps(
            {
                "id": name,
                "name": name.replace("-", " ").title(),
                "domain": "mechanical",
                "tags": ["bracket"],
                "steps": [{"index": 1, "instruction": "A bracket.", "code": "steps/01.py"}],
            },
            indent=2,
        )
    )
    (item / "source.json").write_text(json.dumps({"license": "MIT"}, indent=2))
    return item


def packaged(tmp_path: Path, name: str = "l-bracket", *, step_source: str = STEP_SOURCE) -> bytes:
    """A `.cls` built somewhere other than this machine's catalog.

    Authored outside `catalog_root` on purpose: packing from inside it would be
    re-importing something already installed, which is the one case the id check
    is there to refuse.
    """
    return packed(write_item(tmp_path / "authored", name, step_source=step_source))


def with_an_entry_named(payload: bytes, name: str, blob: bytes = b"held here\n") -> bytes:
    """The same package, plus one entry whose name is the thing under test.

    Rebuilt by hand because the packer cannot produce these: it reads a real
    tree, and no tree holds them. A package delivered directly was not built by
    our packer either.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(payload)) as source, zipfile.ZipFile(buffer, "w") as out:
        for info in source.infolist():
            out.writestr(info.filename, source.read(info))
        out.writestr(name, blob)
    return buffer.getvalue()


def with_an_entry_under_a_file(payload: bytes) -> bytes:
    """The same package, plus an entry that needs an existing file to be a
    directory — which no tree holds."""
    return with_an_entry_named(payload, "artifacts/01/model.stl/extra.bin", b"under a file\n")


def post(client: TestClient, payload: bytes, **params: str):
    return client.post(
        "/packages/import",
        content=payload,
        headers={"Content-Type": "application/octet-stream"},
        params=params,
    )


def imported_items() -> list[Path]:
    root = imported_catalog_root()
    return sorted(root.glob("*/*/manifest.json")) if root.is_dir() else []


@pytest.mark.parametrize("attempt", (1, 2))
def test_two_imports_of_one_id_leave_a_single_project(client, tmp_path, monkeypatch, attempt):
    """Two requests carrying the same id must not both get in.

    Run twice on purpose, each time against a fresh app and a fresh event loop.
    The gate is an ``asyncio.Lock``, and one of those binds itself to the first
    loop that ever contends on it — so a gate shared across apps would raise
    ``RuntimeError: bound to a different event loop`` on the second pass here,
    long after the change that introduced it.

    Checking the id, writing the item and loading it into the store are separate
    steps with awaits between them, so two requests can each pass the check
    before either has recorded anything. The one that loses has to be refused
    outright: a project the ledger never names appears in no catalog listing and
    `clear` cannot reach it, so it would sit there until someone deleted it by
    hand. The delay holds both requests inside the window at once instead of
    leaving it to scheduling.
    """
    payload = packaged(tmp_path)
    real_take_in = packages_router._take_in
    through = []

    def slow_take_in(*args, **kwargs):
        through.append(args)
        time.sleep(0.05)
        return real_take_in(*args, **kwargs)

    monkeypatch.setattr(packages_router, "_take_in", slow_take_in)

    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = sorted(r.status_code for r in pool.map(lambda _: post(client, payload), (1, 2)))

    # The delay is the whole experiment, and it only happens if the import path
    # still reaches `_take_in` through this module. Refactor it into a direct
    # reference and the patch stops biting: both requests would then run to
    # completion one after the other, the second would 409 on an item the first
    # had already finished writing, and the assertions below would still pass
    # while testing nothing. Counting the calls is what tells the two apart.
    assert len(through) == 2, through
    assert codes == [200, 409], codes
    assert len(imported_items()) == 1
    assert len(client.get("/projects").json()) == 1


def test_import_takes_a_package_into_the_catalog_and_makes_it_live(client, tmp_path):
    payload = packaged(tmp_path)

    out = post(client, payload, filename="l-bracket.cls")

    assert out.status_code == 200, out.text
    body = out.json()
    assert body["id"] == "l-bracket", body
    assert body["name"] == "L Bracket", body
    assert body["digest"] == digest_of(payload), body
    assert body["steps_checked"] == 1, body
    # Loaded into the store as part of the import: the autoload runs once at
    # startup, so an item that only reached disk would not be there to open
    # until the next restart.
    assert isinstance(body["project_id"], int), body
    listed = client.get("/catalog", params={"limit": 200}).json()
    assert "L Bracket" in {item["name"] for item in listed["items"]}, listed


def provenance() -> dict:
    """What the imported item says about where it came from."""
    return json.loads((imported_items()[0].parent / "source.json").read_text())


def test_import_records_the_file_the_package_arrived_in(client, tmp_path):
    out = post(client, packaged(tmp_path), filename="from-a-colleague.cls")

    assert out.status_code == 200, out.text
    # The provenance is the import — where this copy came from — and not a claim
    # about who authored the item, which the package does not carry.
    assert provenance()["dataset"] == "imported from the file from-a-colleague.cls"


def test_import_still_records_a_provenance_when_no_file_was_named(client, tmp_path):
    # An item whose origin reads as empty would look like one nobody can account
    # for, which is a stronger claim than "we were not told the name".
    assert post(client, packaged(tmp_path)).status_code == 200
    assert provenance()["dataset"] == "imported from a file on this machine"


def test_import_records_no_remote_reference_for_a_package_handed_over(client, tmp_path):
    """This package came through no listing, so there is no id to carry. An
    empty reference would be a claim of its own — a listing this has none of —
    and a panel would offer to check it for updates against nothing."""
    assert post(client, packaged(tmp_path), filename="from-a-colleague.cls").status_code == 200
    # Asserted as the whole key set rather than the absence of one name. A
    # named absence only discriminates while something writes that name, and
    # nothing here does — this fails the moment any arrival key at all leaks
    # into the hand-over path, whatever it is spelled.
    assert set(provenance()) <= {"dataset", "representation", "license", "id", "note"}


def test_import_keeps_control_characters_out_of_what_it_records(client, tmp_path):
    # The name is the sender's text and is shown to whoever opens the item.
    assert post(client, packaged(tmp_path), filename="l-bracket\r\n\x00.cls").status_code == 200
    assert provenance()["dataset"] == "imported from the file l-bracket.cls"


def test_import_refuses_a_package_that_does_not_match_the_digest_it_was_given(client, tmp_path):
    out = post(client, packaged(tmp_path), expected_digest="0" * 64)

    assert out.status_code == 400, out.text
    assert "digest" in out.json()["detail"], out.text
    assert imported_items() == []


def test_import_confirms_the_digest_only_when_one_was_offered(client, tmp_path):
    payload = packaged(tmp_path, "flat-washer")

    unchecked = post(client, packaged(tmp_path, "l-bracket")).json()
    checked = post(client, payload, expected_digest=digest_of(payload)).json()

    # Saying "verified" with nothing to verify against would be the one claim
    # this endpoint must never make.
    assert unchecked["digest_confirmed"] is False, unchecked
    assert checked["digest_confirmed"] is True, checked


def test_import_refuses_code_the_gate_rejects_and_writes_nothing(client, tmp_path):
    out = post(client, packaged(tmp_path, step_source=BANNED_SOURCE))

    assert out.status_code == 400, out.text
    detail = out.json()["detail"]
    # Which file, and why — a refusal nobody can act on sends them guessing.
    assert "steps/01.py" in detail and "os" in detail, detail
    assert imported_items() == []


def test_import_refuses_an_id_this_machine_already_answers_to(client, tmp_path):
    # The ledger keys on the manifest id, so an import sharing one would take
    # over the item already here at the next load.
    write_item(settings.catalog_root / "mech-catalog")

    out = post(client, packaged(tmp_path))

    assert out.status_code == 409, out.text
    assert "l-bracket" in out.json()["detail"], out.text


def test_import_refuses_a_body_larger_than_a_package_may_be(client, tmp_path, monkeypatch):
    # Counted as it arrives. Reading the whole body first and measuring it after
    # is a limit that has already spent whatever it was meant to protect.
    monkeypatch.setattr(packages_router, "MAX_IMPORT_BYTES", 64)

    out = post(client, packaged(tmp_path))

    assert out.status_code == 413, out.text
    assert imported_items() == []


def _spools_opened(monkeypatch) -> list:
    """Every body buffer the endpoint opens while a test runs."""
    opened = []
    real = tempfile.SpooledTemporaryFile

    def recorded(*args, **kwargs):
        spool = real(*args, **kwargs)
        opened.append(spool)
        return spool

    monkeypatch.setattr(packages_router.tempfile, "SpooledTemporaryFile", recorded)
    return opened


@pytest.mark.parametrize("threshold", [0, 4 * 1024 * 1024])
def test_a_body_the_endpoint_refuses_leaves_no_file_behind(
    client, tmp_path, monkeypatch, threshold
):
    """The body is written down before it can be judged, so it has to be undone.

    Run at both thresholds because they are different situations: at zero the
    body is a real file by the time the limit is exceeded, and a refusal that
    only closed an in-memory buffer would leave that one on disk for every
    oversized request — which is the same route, and takes no credentials.
    """
    monkeypatch.setattr(packages_router, "MAX_IMPORT_BYTES", 64)
    monkeypatch.setattr(packages_router, "SPOOL_THRESHOLD_BYTES", threshold)
    opened = _spools_opened(monkeypatch)

    out = post(client, packaged(tmp_path))

    assert out.status_code == 413, out.text
    assert opened, "the body was never spooled"
    assert all(spool.closed for spool in opened)


def test_only_so_many_packages_are_taken_in_at_once(client, tmp_path, monkeypatch):
    """The limit that bounds what a machine holds for callers who never signed in.

    One at a time, no waiting, and three requests: two of them have to be turned
    away rather than queued. `_take_in` is slowed so all three are in flight at
    once — without that this only measures how quickly they finish.
    """
    monkeypatch.setattr(packages_router, "MAX_CONCURRENT_IMPORTS", 1)
    monkeypatch.setattr(packages_router, "IMPORT_ADMISSION_TIMEOUT", 0.0)
    real_take_in = packages_router._take_in

    def slow_take_in(*args, **kwargs):
        time.sleep(0.3)
        return real_take_in(*args, **kwargs)

    monkeypatch.setattr(packages_router, "_take_in", slow_take_in)
    payloads = [packaged(tmp_path, f"bracket-{n}") for n in range(3)]

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda p: post(client, p), payloads))

    codes = sorted(r.status_code for r in results)
    assert codes == [200, 503, 503], [r.text for r in results]
    refused = next(r for r in results if r.status_code == 503)
    assert refused.headers["Retry-After"] == "1"


def test_a_body_that_never_finishes_arriving_is_refused(monkeypatch):
    """A sender who stops sending must not keep its place for as long as it likes.

    The admission limit only protects anything if a place cannot be held for
    free. Four connections that send headers and then trickle — or stop — would
    otherwise occupy every place there is on a route that takes no credentials,
    and the limit would become the thing worth attacking rather than the thing
    doing the protecting.

    A deadline over the whole body, not a gap between chunks: a sender that
    answers just before each gap expires never trips an idle timer, and holding
    a place is what it is after.

    Driven against the read itself rather than through the client, because the
    test client sends a request body synchronously and cannot hold one open. The
    place is handed back by the same path a refused oversized body takes, which
    `test_a_turn_is_given_back_when_an_import_is_over` covers.
    """
    monkeypatch.setattr(packages_router, "IMPORT_BODY_TIMEOUT", 0.05)
    opened = _spools_opened(monkeypatch)

    class NeverEnds:
        async def stream(self):
            yield b"PK\x03\x04"
            await asyncio.sleep(3600)

    with pytest.raises(HTTPException) as refusal:
        asyncio.run(packages_router._received(NeverEnds()))

    assert refusal.value.status_code == 408
    assert opened, "the body was never spooled"
    assert all(spool.closed for spool in opened), "a stalled body left its file behind"


def test_a_turn_is_given_back_when_an_import_is_over(client, tmp_path, monkeypatch):
    """One at a time has to mean one at a time, not one ever.

    Sequential rather than concurrent on purpose: a turn that is taken and never
    returned still lets the first import through, and every other test here uses
    a freshly built app. Nothing would notice until the machine had served as
    many imports as it allows at once and then stopped answering.

    The refusal in the middle is the other half — a request that ends badly has
    to give its turn back too, or one oversized package retires a slot.
    """
    monkeypatch.setattr(packages_router, "MAX_CONCURRENT_IMPORTS", 1)
    monkeypatch.setattr(packages_router, "IMPORT_ADMISSION_TIMEOUT", 0.0)

    first = post(client, packaged(tmp_path, "bracket-a"))
    monkeypatch.setattr(packages_router, "MAX_IMPORT_BYTES", 64)
    refused = post(client, packaged(tmp_path, "bracket-b"))
    monkeypatch.setattr(packages_router, "MAX_IMPORT_BYTES", MAX_UNCOMPRESSED_BYTES)
    third = post(client, packaged(tmp_path, "bracket-c"))

    assert first.status_code == 200, first.text
    assert refused.status_code == 413, refused.text
    assert third.status_code == 200, third.text


def test_an_idle_machine_takes_a_package_even_with_no_wait_configured(
    client, tmp_path, monkeypatch
):
    """No wait means do not queue, not refuse everything.

    Asking to wait zero seconds for a turn that is already free must still get
    it. Waiting on a timer with no time left cancels the request for a turn
    before it has run, which reads as contention on a machine doing nothing —
    so the free case is taken without involving a timer at all.
    """
    monkeypatch.setattr(packages_router, "IMPORT_ADMISSION_TIMEOUT", 0.0)

    out = post(client, packaged(tmp_path))

    assert out.status_code == 200, out.text


def test_a_package_turned_away_was_never_read(client, tmp_path, monkeypatch):
    """Refusing before the read is the whole reason this limit sits where it does.

    A limit taken after the body is here has already spent the memory it exists
    to save — which is what the catalog gate does, and why it is not this. If a
    turned-away request had been read from, the count below would not be zero.
    """
    monkeypatch.setattr(packages_router, "MAX_CONCURRENT_IMPORTS", 0)
    monkeypatch.setattr(packages_router, "IMPORT_ADMISSION_TIMEOUT", 0.0)
    opened = _spools_opened(monkeypatch)

    out = post(client, packaged(tmp_path))

    assert out.status_code == 503, out.text
    assert opened == [], "the body was read despite being turned away"


def test_a_body_that_spilled_to_disk_is_imported_the_same(client, tmp_path, monkeypatch):
    """Past the threshold the reader is working off a file, not a buffer.

    Everything else here exercises the in-memory side, because every package
    these tests build is small. This is the other path, and it is the one a
    package near the size limit actually takes.
    """
    monkeypatch.setattr(packages_router, "SPOOL_THRESHOLD_BYTES", 0)
    opened = _spools_opened(monkeypatch)

    out = post(client, packaged(tmp_path))

    assert out.status_code == 200, out.text
    assert out.json()["id"] == "l-bracket"
    assert len(imported_items()) == 1
    assert all(spool.closed for spool in opened)


def test_import_reports_an_unwritable_catalog_as_this_machine_s_problem(client, tmp_path):
    # A catalog directory that cannot be written is a deployment fact. Reporting
    # it as a bad package would send someone editing a file that was never wrong.
    imported_catalog_root().write_bytes(b"not a directory\n")

    out = post(client, packaged(tmp_path))

    assert out.status_code == 500, out.text
    assert "not writable" in out.json()["detail"], out.text


def test_the_unwritable_report_keeps_this_machine_s_paths_to_itself(client, tmp_path):
    """Nothing signs in to reach this endpoint, so whatever it says is public.

    A refusal that names where the catalog lives hands the layout of the data
    directory to anyone who can post a package — and the caller can do nothing
    with it, since this failure is not theirs to fix. What went wrong belongs
    in the log, where the person who can fix it is already looking.
    """
    imported_catalog_root().write_bytes(b"not a directory\n")

    out = post(client, packaged(tmp_path))

    assert out.status_code == 500, out.text
    detail = out.json()["detail"]
    assert "not writable" in detail, detail
    assert str(settings.data_dir) not in detail, detail
    assert str(tmp_path) not in detail, detail
    assert "Errno" not in detail, detail


def test_the_report_that_an_import_could_not_start_says_no_more_than_that(client, tmp_path):
    """The same, one step earlier: staging is prepared before anything is
    unpacked, and the directory it wants is inside the data directory too."""
    (settings.data_dir / ".importing").write_bytes(b"not a directory\n")

    out = post(client, packaged(tmp_path))

    assert out.status_code == 500, out.text
    detail = out.json()["detail"]
    assert str(settings.data_dir) not in detail, detail
    assert str(tmp_path) not in detail, detail
    assert "Errno" not in detail, detail


def test_import_refuses_a_package_whose_entries_cannot_both_be_written(client, tmp_path):
    # The opposite of the case above: the package is what is wrong, and whoever
    # holds it can fix it by renaming one entry. Reported as this machine's
    # failure it would send them nowhere, so it is refused before anything is
    # written and named as a package fault.
    out = post(client, with_an_entry_under_a_file(packaged(tmp_path)))

    assert out.status_code == 400, out.text
    detail = out.json()["detail"]
    assert "artifacts/01/model.stl" in detail, detail
    assert "artifacts/01/model.stl/extra.bin" in detail, detail
    assert imported_items() == []


def test_import_refuses_a_name_segment_no_filesystem_could_hold(client, tmp_path):
    # Also the package's fault, and also fixable by renaming: a part of the name
    # is inside the character limit and outside the byte one every filesystem
    # measures a segment in. Left to the write it arrives as this machine failing — with
    # the path it failed on, which is this machine's business and not the
    # caller's.
    name = "artifacts/" + "\U0001f600" * 64  # 74 characters, one segment of 256 bytes

    out = post(client, with_an_entry_named(packaged(tmp_path), name))

    assert out.status_code == 400, out.text
    assert imported_items() == []


def test_the_conflict_report_keeps_this_machine_s_paths_to_itself(client, tmp_path):
    """A conflict is the caller's to act on; where the item sits is not.

    `L Bracket` and `l-bracket` are different items by every check that reads
    the id and the same directory once it is folded into one, so the second
    import collides on the directory rather than on the id — the branch that
    had the absolute path in it.
    """
    assert post(client, packaged(tmp_path, "l-bracket")).status_code == 200

    out = post(client, packaged(tmp_path, "L Bracket"))

    assert out.status_code == 409, out.text
    detail = out.json()["detail"]
    assert str(settings.data_dir) not in detail, detail
    assert str(tmp_path) not in detail, detail


@pytest.mark.parametrize("sites", [1, 2], ids=["local-header-only", "both-headers"])
def test_import_refuses_a_name_that_is_not_the_utf8_it_says_it_is(client, tmp_path, sites):
    # The package is what is wrong — a name flagged UTF-8 that is not — but the
    # decode raises out of reading the archive rather than out of a check, so it
    # used to travel past the reader's own error type and arrive as a 500.
    # A non-ASCII entry first, because that is what makes the archive set the
    # flag that sends `zipfile` down the decoding path at all. The name is
    # written in two headers and each is decoded somewhere different, so both
    # are corrupted here in turn.
    payload = with_an_entry_named(packaged(tmp_path), "artifacts/é.stl")
    broken = payload.replace("artifacts/é.stl".encode(), b"artifacts/\xff\xfe.stl", sites)
    assert len(broken) == len(payload)

    out = post(client, broken)

    assert out.status_code == 400, out.text
    assert imported_items() == []


def test_import_refuses_a_member_it_cannot_open(client, tmp_path):
    # The same fault reported by the same route, reached by a different trigger:
    # here every name is fine and it is *opening* a member that `zipfile` will
    # not do. An encrypted entry raises `RuntimeError` asking for a password,
    # which is no more a `BadZipFile` than the decode above, so it too used to
    # arrive as this machine having failed on a package its holder could repack.
    #
    # Edited by hand rather than packed, because `writestr` sets the general
    # purpose bits itself. Bit 0 is the one that says a member is encrypted.
    broken = rewritten(packaged(tmp_path), "artifacts/01/model.stl", flag=0x1)

    out = post(client, broken)

    assert out.status_code == 400, out.text
    assert "encrypted" in out.json()["detail"]
    assert imported_items() == []


def test_a_failure_outside_the_handled_ones_says_nothing_about_this_machine(tmp_path, monkeypatch):
    """The catch-all sitting behind every route.

    `OSError` carries the filename it failed on, and loading the written item
    into the store happens after the block that was hardened for that — so a
    path could still reach an endpoint nobody signs in to, by the one route
    that answers when no route knew what to say.

    Its own client, because the shared one re-raises a server error instead of
    letting the handler answer — which is the very thing being tested here.
    """
    payload = packaged(tmp_path)

    async def gives_way(*args, **kwargs):
        raise OSError(errno.ENOSPC, "No space left on device", str(tmp_path / "store" / "a.stl"))

    monkeypatch.setattr(packages_router, "load_house", gives_way)
    store = Store(db_path=tmp_path / "unhandled.sqlite", artifacts_dir=tmp_path / "unhandled")

    with TestClient(create_app(store=store), raise_server_exceptions=False) as client:
        out = post(client, payload)

    assert out.status_code == 500, out.text
    detail = out.json()["detail"]
    assert str(tmp_path) not in detail, detail
    assert "Errno" not in detail, detail


def test_import_refuses_something_that_is_not_a_package_at_all(client):
    out = post(client, b"this is not a zip")

    assert out.status_code == 400, out.text
    assert imported_items() == []


def test_import_will_not_take_over_an_item_whose_directory_went_missing(client, tmp_path):
    """The ledger is what decides a takeover, and it outlives the directory.

    Removing an item's directory without clearing it — a sample dropped from a
    newer image, an operator tidying up — leaves the entry behind. `load_house`
    finds it, sees content that does not match, and clears the project it named.
    So an import that only asked the disk would arrive as a stranger and delete
    somebody's project on its way in.
    """
    payload = packaged(tmp_path, "l-bracket")
    assert post(client, payload).status_code == 200
    before = client.get("/catalog", params={"limit": 200}).json()["items"]
    shutil.rmtree(imported_items()[0].parent)

    out = post(client, payload)

    assert out.status_code == 409, out.text
    after = client.get("/catalog", params={"limit": 200}).json()["items"]
    assert [item["project_id"] for item in after] == [item["project_id"] for item in before]


def test_import_refuses_a_body_that_does_not_declare_itself_a_package(client, tmp_path):
    """A raw body a browser will post without asking is a way in for any page.

    Every other write endpoint here takes a JSON body, which is not a request a
    browser makes cross-origin without a preflight it can be refused at. A body
    this endpoint accepts under `text/plain` is a form post in everything but
    name — a page the user merely visits could plant a package in their catalog.
    """
    out = client.post(
        "/packages/import",
        content=packaged(tmp_path),
        headers={"Content-Type": "text/plain"},
    )

    assert out.status_code == 415, out.text
    assert imported_items() == []


def test_a_freshly_imported_item_is_read_only_the_moment_it_lists(client, tmp_path):
    """No window where a new catalog item is on the list as an ordinary project.

    The loader marks the row in the insert that creates it, so "loaded" and
    "read-only" arrive together. This used to be two answers assembled from two
    places — a snapshot of the sidecar ledger, then the projects — and a list
    landing between an import's two halves offered the user edit controls for an
    item the server would then refuse to edit.
    """
    assert post(client, packaged(tmp_path)).status_code == 200

    rows = client.get("/projects").json()

    assert [row["is_catalog"] for row in rows] == [True], rows


def test_the_project_list_does_not_ask_the_ledger_whether_a_project_is_catalog(client, tmp_path):
    """Losing the ledger must not turn catalog items editable.

    Membership is ``projects.catalog_item_id`` (#27); the ledger beside the db
    holds what the catalog panel *displays*. Taking that file away is what tells
    the two apart: a list that consulted it would report every row here as an
    ordinary project, which is the answer that hands over the edit controls —
    and the same answer a stale snapshot gives for an import that just finished.
    """
    assert post(client, packaged(tmp_path)).status_code == 200
    # Where `backend.catalog_state.ledger_for` puts it: beside the store's db,
    # which the `client` fixture opens at `tmp_path / "db.sqlite"`.
    ledger = tmp_path / "catalog-ledger.json"
    assert ledger.exists(), "the import should have recorded the item's details"
    ledger.unlink()

    rows = client.get("/projects").json()

    assert [row["is_catalog"] for row in rows] == [True], rows


def test_a_cancelled_import_does_not_leave_the_next_one_to_collide(tmp_path, monkeypatch):
    """Giving the gate up on cancellation is not the same as being finished.

    `asyncio.to_thread` hands the write to a thread cancelling cannot reach, so
    a cancelled import lets go of the gate with its worker still on the way to
    `staging.rename`. The next one then passes every check — nothing is on disk
    yet, nothing is in the store — and the two arrive at the same directory.
    Renaming onto one that has since been filled is an OSError, reported as this
    machine's failure: a 500 where the honest answer is that something here
    answers to that name, which is the 409 the gate exists to produce.

    Nothing cancels a handler mid-import today: the body clock stops before the
    gate is taken and there is no timeout middleware. A timeout middleware is a
    few lines, and this is the failure it would arm, silently.

    Staged inside `_write_entries` because that is the window — past the checks,
    short of the rename. Wait on the gate and both are through the write before
    either could interleave; wait on `_take_in` and the loser is refused by the
    directory the winner has already put in place.
    """
    payload = packaged(tmp_path)
    unstaged_write_entries = catalog_importer._write_entries
    reached = (threading.Event(), threading.Event())
    may_finish = (threading.Event(), threading.Event())
    turns = itertools.count()

    def staged_write_entries(package, staging):
        turn = next(turns)
        if turn < len(reached):
            reached[turn].set()
            may_finish[turn].wait(20)
        return unstaged_write_entries(package, staging)

    monkeypatch.setattr(catalog_importer, "_write_entries", staged_write_entries)

    async def go():
        store = Store(db_path=tmp_path / "db.sqlite", artifacts_dir=tmp_path / "artifacts")
        await store.init()
        request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(import_gate=asyncio.Lock()))
        )
        placing = {"store": store, "expected_digest": None}

        first = asyncio.create_task(
            packages_router.place_package(request, origin="first.cls", payload=payload, **placing)
        )
        # Asserted, not awaited and forgotten: everything below is arranged
        # around one import standing in the write window, so a staging hook that
        # stopped being reached would leave this passing on nothing.
        assert await asyncio.to_thread(reached[0].wait, 20), "the staging hook never ran"
        # Not awaited here: with the gate held to the end of the worker, that
        # would wait on a write this test has not released yet.
        first.cancel()

        second = asyncio.create_task(
            packages_router.place_package(request, origin="second.cls", payload=payload, **placing)
        )
        # Bounded, and expected to time out once this is fixed: the second is
        # then waiting on the gate rather than standing in the write window.
        await asyncio.to_thread(reached[1].wait, 1.0)

        may_finish[0].set()
        for _ in range(200):
            if imported_items():
                break
            await asyncio.sleep(0.05)
        may_finish[1].set()

        with contextlib.suppress(asyncio.CancelledError):
            await first
        return await second

    with pytest.raises(HTTPException) as refused:
        asyncio.run(go())

    assert refused.value.status_code == 409, refused.value.detail
    assert len(imported_items()) == 1


def test_overwrite_makes_room_for_an_item_that_is_already_here(tmp_path):
    """The replace path, which only `place_package(overwrite=True)` reaches.

    No route in this tree passes it. It is the seam a build that fetches a newer
    version of something already held uses, and it is covered here rather than
    through a route because the tests that covered it left with the route that
    had one. `_make_room` removes catalog item directories to free a name, which
    is not a thing to leave with no test at all.
    """
    payload = packaged(tmp_path)

    async def go():
        store = Store(db_path=tmp_path / "db.sqlite", artifacts_dir=tmp_path / "artifacts")
        await store.init()
        request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(import_gate=asyncio.Lock()))
        )
        placing = {"store": store, "expected_digest": None}

        first = await packages_router.place_package(
            request, origin="from-a-colleague.cls", payload=payload, **placing
        )
        # The same id again is refused, which is what makes the flag mean
        # something rather than being the only way through.
        with pytest.raises(HTTPException) as refused:
            await packages_router.place_package(
                request, origin="again.cls", payload=payload, **placing
            )
        second = await packages_router.place_package(
            request, origin="again.cls", payload=payload, overwrite=True, **placing
        )
        return first, refused.value, second

    first, refused, second = asyncio.run(go())

    assert refused.status_code == 409, refused.detail
    assert first["id"] == second["id"]
    # Replaced, not accumulated: one item on disk, not two directories fighting
    # over the same id.
    assert len(imported_items()) == 1


def test_overwrite_does_not_reach_past_the_received_root(tmp_path):
    """A bundled item is the product's own, mounted read-only in the shipped
    image. `_make_room` frees a name among received items and must not treat a
    bundled one as room to be made."""
    bundled = settings.domain_catalog_dir("mechanical") / "l-bracket"
    (bundled / "steps").mkdir(parents=True, exist_ok=True)
    (bundled / "steps" / "01.py").write_text("result = 1\n")
    (bundled / "manifest.json").write_text(
        json.dumps(
            {
                "id": "l-bracket",
                "name": "L-Bracket",
                "domain": "mechanical",
                "steps": [{"index": 1, "instruction": "A bracket.", "code": "steps/01.py"}],
            }
        )
    )
    payload = packaged(tmp_path)

    async def go():
        store = Store(db_path=tmp_path / "db.sqlite", artifacts_dir=tmp_path / "artifacts")
        await store.init()
        request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(import_gate=asyncio.Lock()))
        )
        return await packages_router.place_package(
            request,
            origin="again.cls",
            payload=payload,
            store=store,
            expected_digest=None,
            overwrite=True,
        )

    with pytest.raises(HTTPException) as refused:
        asyncio.run(go())

    # 403, not 409: the id is not merely taken, it is taken by something this
    # endpoint has no business replacing.
    assert refused.value.status_code == 403, refused.value.detail
    # Refused with the bundled item still on disk, untouched.
    assert (bundled / "manifest.json").exists()
