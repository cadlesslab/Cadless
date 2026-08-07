"""Sidecar ledger tests (catalog Phase 1)."""

import errno
import logging
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from cadless.catalog import ledger as ledger_module
from cadless.catalog.ledger import Ledger

REPO_ROOT = Path(__file__).resolve().parents[1]

# One recorder, in a process of its own, holding its write open for `delay`
# seconds. The delay goes inside `_write` so it lands between the read and the
# write of one read-modify-write — the window two recorders have to be in at
# once for either to lose. Patched here rather than in the ledger because the
# thing under test is what happens between separate processes, and a process
# started for this can only be told what to do by the source it runs.
RECORDER = """
import sys, time
from pathlib import Path
from cadless.catalog.ledger import Ledger

path, house_id, delay, gate = sys.argv[1], sys.argv[2], float(sys.argv[3]), Path(sys.argv[4])
unpatched = Ledger._write

def slow(self, data):
    time.sleep(delay)
    unpatched(self, data)

Ledger._write = slow

# Neither recorder starts until both are here. Leaving the overlap to how long
# each interpreter took to come up would make the whole experiment a matter of
# scheduling luck on a loaded machine -- and the way it would fail is by both
# passing, having never been in the window together.
(gate / house_id).write_text("here")
deadline = time.monotonic() + 30
while len(list(gate.iterdir())) < 2:
    if time.monotonic() > deadline:
        raise SystemExit("the other recorder never arrived")
    time.sleep(0.01)

Ledger(Path(path)).record(house_id, 1)
"""


def test_fresh_ledger_is_empty(tmp_path):
    led = Ledger(tmp_path / "ledger.json")
    assert led.entries() == {}
    assert led.get("x") is None


def test_record_get_and_persist(tmp_path):
    path = tmp_path / "ledger.json"
    led = Ledger(path)
    led.record("h", 3)
    entry = led.get("h")
    assert entry["step_count"] == 3
    assert isinstance(entry["loaded_at"], str) and entry["loaded_at"]
    assert path.exists()
    # A second handle reads back persisted state.
    assert Ledger(path).get("h")["step_count"] == 3


def test_record_does_not_keep_a_project_id(tmp_path):
    """Which project an item became lives on the project row and only there.

    Two records of one fact can disagree, and this is the copy that can be lost
    or half-written — so anything asking "is this project a catalog item?" would
    be asking the wrong one.
    """
    led = Ledger(tmp_path / "ledger.json")
    led.record("h", 3, name="Casa")
    assert "project_id" not in led.get("h")


def test_record_replaces_same_id(tmp_path):
    led = Ledger(tmp_path / "ledger.json")
    led.record("h", 1)
    led.record("h", 4)
    assert list(led.entries()) == ["h"]
    assert led.get("h")["step_count"] == 4


def test_remove_is_noop_when_absent(tmp_path):
    led = Ledger(tmp_path / "ledger.json")
    led.record("h", 1)
    led.remove("h")
    assert led.get("h") is None
    led.remove("h")  # no error
    assert led.entries() == {}


def test_record_discovery_metadata(tmp_path):
    """category/tags/description/thumbnail persist when provided (#21)."""
    led = Ledger(tmp_path / "ledger.json")
    led.record(
        "h",
        3,
        name="Casa",
        domain="house",
        category="bungalow",
        tags=["garage", "two-storey"],
        description="Cosy.",
        thumbnail=True,
    )
    entry = led.get("h")
    assert entry["category"] == "bungalow"
    assert entry["tags"] == ["garage", "two-storey"]
    assert entry["description"] == "Cosy."
    assert entry["thumbnail"] is True


def test_record_without_metadata_keeps_legacy_shape(tmp_path):
    """Omitted metadata is absent rather than null (#21)."""
    led = Ledger(tmp_path / "ledger.json")
    led.record("h", 3, name="Casa", domain="house")
    entry = led.get("h")
    assert set(entry) == {"step_count", "loaded_at", "name", "domain"}


def test_record_source_and_content_hash(tmp_path):
    """Provenance + content hash persist when provided (#23)."""
    led = Ledger(tmp_path / "ledger.json")
    provenance = {"dataset": "deepcad", "id": "0001", "license": "L"}
    led.record("h", 3, source=provenance, content_hash="sha256:abc")
    entry = led.get("h")
    assert entry["source"] == provenance
    assert entry["content_hash"] == "sha256:abc"
    # omitted -> absent (legacy shape preserved)
    led.record("h2", 1)
    assert "source" not in led.get("h2") and "content_hash" not in led.get("h2")


def test_concurrent_record_keeps_both_entries(tmp_path, monkeypatch):
    """Two recorders at once must not drop each other's entry.

    ``record`` reads the whole file, adds its entry and writes the whole file
    back. That used to be safe by accident: it ran inline on the event loop,
    where nothing could interleave. The loader now hands it to a worker thread,
    so two imports can be inside it at the same time, and whoever writes last
    would carry only what it read at the start. The delay holds the window open
    rather than leaving it to scheduling luck.
    """
    led = Ledger(tmp_path / "ledger.json")
    unlocked_write = Ledger._write

    def slow_write(self, data):
        time.sleep(0.05)
        unlocked_write(self, data)

    monkeypatch.setattr(Ledger, "_write", slow_write)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda n: led.record(f"h{n}", 1), (1, 2)))
    assert set(led.entries()) == {"h1", "h2"}


def test_a_read_does_not_wait_on_the_write_lock(tmp_path):
    """Readers must not queue behind a writer: one of them is the event loop.

    ``backend.catalog_state`` resolves the ledger inline inside async routes, so
    an acquire there parks the whole loop — not just that request — until the
    holder lets go. Reads do not need the lock because a write swaps the file in
    whole, so this pins them to never taking it.
    """
    led = Ledger(tmp_path / "ledger.json")
    led.record("h", 1)
    read_finished = threading.Event()

    def read():
        led.entries()
        read_finished.set()

    reader = threading.Thread(target=read)
    with ledger_module._FILE_LOCK:
        reader.start()
        assert read_finished.wait(timeout=2.0), "a read waited for the write lock"
    reader.join()


def test_a_read_during_a_write_never_sees_half_a_ledger(tmp_path, monkeypatch):
    """A write must swap a finished file in, not rebuild the live one in place.

    Rewriting the live path empties it first, and a reader landing in that window
    gets an empty ledger back — for as long as it lasts every catalog project
    reads as an ordinary one, so the API would offer to edit items it means to
    keep read-only. Writing beside it and moving it over leaves readers the
    previous copy until the next one is whole.
    """
    led = Ledger(tmp_path / "ledger.json")
    led.record("first", 1)
    real_write_text = Path.write_text

    def empty_then_write(self, data, *args, **kwargs):
        real_write_text(self, "")  # what a rewrite in place leaves mid-flight
        time.sleep(0.05)
        return real_write_text(self, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", empty_then_write)
    seen: list[set[str]] = []
    writer = threading.Thread(target=lambda: led.record("second", 1))
    reader = threading.Thread(target=lambda: seen.append(set(led.entries())))
    writer.start()
    time.sleep(0.01)  # let the writer reach the middle of its write
    reader.start()
    writer.join()
    reader.join()
    assert seen in ([{"first"}], [{"first", "second"}])


def _record_together(path: Path, ids: tuple[str, ...], delay: float, gate: Path) -> None:
    """Record each id from a process of its own, in the window together.

    ``PYTHONPATH`` is prepended rather than set: the suite reaches this tree
    through pytest's own path setting, which a process started from here does
    not inherit, and replacing whatever the environment already had would be a
    second change nobody asked for. Pointing it at the checkout under test is
    what keeps a worktree's run from silently exercising some other copy.
    """
    gate.mkdir(parents=True, exist_ok=True)
    inherited = os.environ.get("PYTHONPATH")
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(filter(None, (str(REPO_ROOT), inherited))),
    }
    running = [
        subprocess.Popen(
            [sys.executable, "-c", RECORDER, str(path), house_id, str(delay), str(gate)],
            cwd=REPO_ROOT,
            env=env,
        )
        for house_id in ids
    ]
    try:
        for process in running:
            assert process.wait(timeout=60) == 0, "a recorder did not finish its write"
    finally:
        # A recorder still up here is one the rendezvous never released; leaving
        # it would hold the ledger open for whatever runs next.
        for process in running:
            if process.poll() is None:
                process.kill()
                process.wait()
    assert {entry.name for entry in gate.iterdir()} == set(ids), "the two never met"


def test_two_processes_recording_at_once_keep_both_entries(tmp_path):
    """A CLI run beside the API must not drop what the API just recorded.

    ``record`` is a read-modify-write of the whole file. A lock inside one
    process orders the threads there and says nothing about anyone else, so two
    processes both read, both add their own entry, and the one that writes last
    puts back a ledger that never had the other's. `cadless/catalog/README.md`
    tells people to run `load` inside the api container, which is exactly this.

    Losing the entry is the quiet half. The loud half is that both writers build
    their next ledger under one fixed name beside it, so the first `os.replace`
    can take the file the second is about to move and that one dies on a path
    nothing holds any more. Nothing catches that: `load_house` answers for a
    ledger it cannot parse, not for one whose write collided, so it travels up
    and the import that was recording reports 500. Both halves are the same
    missing thing — an order the two processes agree on — so this asks for the
    end state, and either half failing to hold leaves it unmet.

    What an entry costs is what the catalog panel shows about an item; the
    project itself is marked in the db and stays read-only. It comes back on the
    next load, since an entry that is not there has no content hash to compare.
    """
    path = tmp_path / "ledger.json"

    _record_together(path, ("first", "second"), delay=0.3, gate=tmp_path / "met")

    assert set(Ledger(path).entries()) == {"first", "second"}


def test_a_held_lock_is_given_up_on_rather_than_waited_out(tmp_path, monkeypatch):
    """A writer that cannot have its turn says so instead of holding everything.

    Recording happens with the import gate held, so a wait here is a wait every
    import and every catalog delete on this machine sits behind. Unbounded, that
    hands any process that opens this file the ability to stop the endpoint —
    including one that took the lock and then stopped making progress.

    The lock refused is the whole of what is given up. Nothing half-written is
    left: the entry was never applied, and the ledger reads exactly as before.
    """
    monkeypatch.setattr(ledger_module, "LOCK_WAIT", 0.1)
    led = Ledger(tmp_path / "ledger.json")
    led.record("already-here", 1)
    before = led.entries()

    # A second open file description, which is what `flock` contends on — the
    # same process is enough to stand in for another one.
    with open(led.lock_path, "a") as holder:
        ledger_module.fcntl.flock(holder, ledger_module.fcntl.LOCK_EX)
        started = time.monotonic()
        try:
            led.record("late", 1)
        except ledger_module.LedgerBusy:
            waited = time.monotonic() - started
        else:  # pragma: no cover - the failure this test exists to catch
            raise AssertionError("recorded while another writer held the lock")

    assert waited < 2.0, f"gave the gate away for {waited:g}s"
    assert led.entries() == before


def test_a_filesystem_without_locking_still_gets_its_write(tmp_path, monkeypatch, caplog):
    """No lock to take is not the same as someone holding one.

    A mount that answers ENOLCK would otherwise spend the whole timeout spinning
    and then report a holder that was never there, dropping every entry it was
    asked to record. Ordering across processes is what is lost, which is where
    this code stood before the lock existed — and the alternative is turning a
    deployment on such a mount into one that records nothing.
    """
    monkeypatch.setattr(ledger_module, "_LOCKING_UNAVAILABLE", False)

    def no_locking(handle, operation):
        raise OSError(errno.ENOLCK, "no locks available")

    monkeypatch.setattr(ledger_module.fcntl, "flock", no_locking)
    led = Ledger(tmp_path / "ledger.json")

    started = time.monotonic()
    with caplog.at_level(logging.WARNING):
        led.record("recorded anyway", 1)
    took = time.monotonic() - started

    assert led.get("recorded anyway") is not None
    assert took < 1.0, f"spun for {took:g}s on a lock nobody could take"
    assert "no file locking" in caplog.text, caplog.text
