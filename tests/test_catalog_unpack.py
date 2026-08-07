"""Reading a `.cls` package that someone else wrote.

Everything here arrives from outside: the archive, the entry names, and every
value in `cls.json`. The code inside is then run on this machine, which is why
the reader refuses rather than repairs — a package that is not exactly what the
format allows is not worth guessing about.

The archives are built by hand rather than by the packer, because what needs
testing is what happens when the bytes are *not* what our packer would produce.
"""

from __future__ import annotations

import io
import json
import struct
import zipfile
import zlib

import pytest

from cadless.catalog.pack import (
    CENTRAL_DIRECTORY_RECORD_BYTES,
    EOCD_BYTES,
    MAX_CENTRAL_DIRECTORY_BYTES,
    MAX_ENTRIES,
    MAX_ENTRY_NAME_LENGTH,
    MAX_ENTRY_SEGMENT_BYTES,
    MAX_ZIP_COMMENT_BYTES,
    ZIP64_EOCD_BYTES,
    ClsError,
    _declared_directory_size,
    digest_of,
    read_cls,
    verify_steps,
)
from tests.cls_fixtures import FIXED_DATE_TIME, archive
from tests.zip_records import (
    CENTRAL_EXTRACT_VERSION,
    CENTRAL_HEADER_OFFSET,
    END_DIRECTORY_OFFSET,
    END_RECORD,
    LOCAL_EXTRA_LENGTH,
    records,
    rewritten,
    with_a_scrambled_stream,
)

ALLOWED_STEP = "from build123d import Box\n\nresult = Box(10, 10, 10)\n"
BANNED_STEP = "import os\n\nresult = os.getcwd()\n"

META = {
    "format_version": 1,
    "content_version": "1.0.0",
    "license": "MIT",
    "title": "L-Bracket",
    "tags": ["bracket"],
    "included_fields": ["artifacts", "steps"],
    "cadless_manifest": {
        "id": "l-bracket",
        "name": "L-Bracket",
        "domain": "mechanical",
        "tags": ["bracket"],
        "steps": [{"index": 1, "instruction": "A bracket.", "code": "steps/01.py"}],
    },
}


def sound(meta: dict | None = META, **extra: bytes) -> bytes:
    """A package the reader should accept."""
    members = []
    if meta is not None:
        members.append(("cls.json", json.dumps(meta, sort_keys=True).encode()))
    members.append(("steps/01.py", ALLOWED_STEP.encode()))
    members.append(("artifacts/01/model.stl", b"solid\n"))
    members.extend(extra.items())
    return archive(members)


def at_the_name_limit(stem: str, filler: str) -> str:
    """The longest name the rules allow, written out of `filler`.

    Both bounds at once: the whole name in characters, and no segment of it over
    the byte limit a filesystem imposes. Where the separators fall therefore
    depends on how wide the filler encodes, and one spends a character too.
    """
    name = stem
    while True:
        room = MAX_ENTRY_SEGMENT_BYTES - len(name.rsplit("/", 1)[-1].encode())
        addition = filler if room >= len(filler.encode()) else "/" + filler
        if len(name) + len(addition) > MAX_ENTRY_NAME_LENGTH:
            return name
        name += addition


# --- tamper detection -----------------------------------------------------


def test_a_package_that_does_not_match_its_expected_digest_is_refused():
    """The digest is what the publisher recorded at publication. Checking it
    here is the only thing that notices an edit made after that."""
    tampered = sound()
    with zipfile.ZipFile(io.BytesIO(tampered)) as opened:
        assert opened.read("steps/01.py") == ALLOWED_STEP.encode()

    with pytest.raises(ClsError, match="digest"):
        read_cls(tampered, expected_digest="0" * 64)


def test_a_package_that_matches_its_expected_digest_is_accepted():
    payload = sound()

    assert read_cls(payload, expected_digest=digest_of(payload)).canonical_digest


def test_editing_one_byte_moves_the_digest():
    """Guards the guard: an expected-digest check is only worth having if the
    digest actually moves when the payload does."""
    before = digest_of(sound())
    after = digest_of(
        archive(
            [
                ("cls.json", json.dumps(META, sort_keys=True).encode()),
                ("steps/01.py", ALLOWED_STEP.replace("10, 10, 10", "20, 10, 10").encode()),
                ("artifacts/01/model.stl", b"solid\n"),
            ]
        )
    )

    assert before != after


# --- the layout -----------------------------------------------------------


@pytest.mark.parametrize("stray", ["setup.py", "lib/helper.py", "README.md"])
def test_an_entry_outside_the_layout_is_refused(stray):
    """Anything stored is written to disk and shipped onward. Only the entries
    the format defines may be present, so nothing travels by being unrecognised.
    """
    with pytest.raises(ClsError, match="layout"):
        read_cls(sound(**{stray: b"payload"}))


def test_a_directory_entry_outside_the_layout_is_refused_too():
    """A directory entry is a name with a trailing separator and no content. It
    still lands in whatever unpacks the archive, so the name is checked either
    way — and checking it before the is-a-directory skip is what stops a name
    ending in `/` from slipping past everything below.
    """
    with pytest.raises(ClsError, match="layout"):
        read_cls(archive([("cls.json", json.dumps(META).encode()), ("lib/", b"")]))


@pytest.mark.parametrize(
    "hostile",
    ["../escape.py", "steps/../../escape.py", "/etc/passwd", "steps/a//b.py", "steps/a\x07.py"],
)
def test_a_name_that_could_escape_its_container_is_refused(hostile):
    """These names are written to disk. `extractall` would follow them out of
    the target directory, which is why nothing here uses it."""
    with pytest.raises(ClsError):
        read_cls(archive([("cls.json", json.dumps(META).encode()), (hostile, b"x")]))


def test_a_duplicate_entry_is_refused():
    """Two entries with one name: whichever the reader keeps, something else
    reading the same archive may keep the other."""
    with pytest.raises(ClsError, match="duplicate"):
        read_cls(
            archive(
                [
                    ("cls.json", json.dumps(META).encode()),
                    ("steps/01.py", ALLOWED_STEP.encode()),
                    ("steps/01.py", BANNED_STEP.encode()),
                ]
            )
        )


@pytest.mark.parametrize(
    "first,second",
    [
        ("steps/A.py", "steps/a.py"),
        ("steps/a.py", "steps/A.py"),
        ("artifacts/01/Model.STL", "artifacts/01/model.stl"),
        # The same word, composed and decomposed. A filesystem that compares
        # names in one normal form treats these as one name.
        ("steps/café.py", "steps/café.py"),
    ],
)
def test_entries_one_filesystem_cannot_tell_apart_are_refused(first, second):
    """Two names the reader keeps apart and the disk does not.

    Everything under `steps/` is checked, so neither of these is unchecked
    code — what breaks is the tie between the two. The importer writes each
    entry in the order the central directory lists them, so on a
    case-insensitive filesystem the file that survives takes its *name* from
    the first entry and its *bytes* from the last, and both are chosen by
    whoever built the archive. The package then reports a digest over content
    that is not what landed.
    """
    with pytest.raises(ClsError, match="cannot be told apart"):
        read_cls(
            archive(
                [
                    ("cls.json", json.dumps(META).encode()),
                    (first, b"# the first entry\n"),
                    (second, b"# the second entry\n"),
                ]
            )
        )


@pytest.mark.parametrize(
    "first,second",
    [
        # The file first, then the entry that needs its name to be a directory.
        ("artifacts/b", "artifacts/b/y.bin"),
        # The same pair the other way round: which one the archive lists first
        # decides which write fails, not whether the package is writable.
        ("artifacts/b/y.bin", "artifacts/b"),
        # Two directories deep, so the clash is not only with the last segment.
        ("artifacts/b", "artifacts/b/deeper/y.bin"),
        # Folded, because a case-insensitive filesystem reads these as one name
        # even though the reader keeps them apart.
        ("artifacts/a", "artifacts/A/y.bin"),
        ("steps/01.py", "steps/01.PY/02.py"),
    ],
)
def test_a_name_another_entry_needs_as_a_directory_is_refused(first, second):
    """A package that cannot be written out as a tree at all.

    One entry is a file and another puts a directory in its place, so whichever
    is written first makes the second impossible: a `mkdir` onto a file, or a
    write onto a directory. The whole-name rule above does not see this — the
    two names are different, and it is only the path between them that
    collides. Caught here because the alternative is discovering it halfway
    through writing the item, where the failure reads as this machine being
    unable to write rather than as the package being wrong.
    """
    with pytest.raises(ClsError, match="cannot both be written"):
        read_cls(
            archive(
                [
                    ("cls.json", json.dumps(META).encode()),
                    (first, b"# the first entry\n"),
                    (second, b"# the second entry\n"),
                ]
            )
        )


@pytest.mark.parametrize(
    "first,second",
    [
        ("artifacts/b", "artifacts/b/y.bin"),
        ("artifacts/b/y.bin", "artifacts/b"),
    ],
)
def test_the_refusal_says_which_name_is_the_file(first, second):
    """The message is the whole remedy: whoever holds the package has to know
    which two names to change, and the endpoint hands this text straight on.

    The same sentence whichever order the directory lists them in — which of
    the two is the file is a fact about the package, not about the order it
    was read in, and a refusal that swapped them would send its reader to
    rename the wrong one.
    """
    with pytest.raises(ClsError) as refusal:
        read_cls(
            archive(
                [
                    ("cls.json", json.dumps(META).encode()),
                    (first, b"# the first entry\n"),
                    (second, b"# the second entry\n"),
                ]
            )
        )
    assert str(refusal.value) == (
        "'artifacts/b' is a file and 'artifacts/b/y.bin' is inside it, so they "
        "cannot both be written and this package was not opened"
    )


@pytest.mark.parametrize(
    "directory_entry",
    [
        # What an ordinary zip writer emits for the directory its files sit in.
        "artifacts/01/",
        # The same name as a file entry. Nothing is unpacked from a directory
        # entry, so it takes no name from one and there is no clash to refuse.
        "artifacts/01/model.stl/",
    ],
)
def test_a_directory_entry_is_not_an_entry_claiming_that_name(directory_entry):
    """The rule above must not read a directory entry as a file.

    Most zip writers emit one per directory, so reading them as names would
    refuse packages for having been written by an ordinary tool. This is why
    the rule is asked of the entries that get unpacked and not of the archive's
    whole listing — the placement matters, and nothing else pins it.
    """
    package = read_cls(
        archive(
            [
                ("cls.json", json.dumps(META).encode()),
                ("steps/01.py", ALLOWED_STEP.encode()),
                (directory_entry, b""),
                ("artifacts/01/model.stl", b"solid\n"),
            ]
        )
    )
    assert package.entries["artifacts/01/model.stl"] == b"solid\n"
    assert directory_entry not in package.entries


@pytest.mark.parametrize(
    "first,second",
    [
        # A shared prefix that stops inside a segment is not a shared path.
        ("artifacts/b.bin", "artifacts/b/y.bin"),
        ("artifacts/bb", "artifacts/b/y.bin"),
        # Both are files under the same directory, which is the ordinary case.
        ("artifacts/b/x.bin", "artifacts/b/y.bin"),
        # Directories that fold together are still allowed: both files arrive
        # under the names the manifest reads them back by.
        ("artifacts/a/x.bin", "artifacts/A/y.bin"),
    ],
)
def test_names_that_share_a_prefix_without_colliding_are_accepted(first, second):
    """The rule refuses a name a *directory* has to occupy, and nothing wider.
    A common prefix that is not a whole path segment is two separate files."""
    package = read_cls(
        archive(
            [
                ("cls.json", json.dumps(META).encode()),
                ("steps/01.py", ALLOWED_STEP.encode()),
                (first, b"# the first entry\n"),
                (second, b"# the second entry\n"),
            ]
        )
    )
    assert {first, second} <= set(package.entries)


def test_names_that_only_look_alike_are_still_accepted():
    """The rule folds case and normalization, and nothing else. Two entries
    that merely resemble each other are different files everywhere."""
    package = read_cls(
        archive(
            [
                ("cls.json", json.dumps(META).encode()),
                ("steps/01.py", ALLOWED_STEP.encode()),
                ("steps/O1.py", ALLOWED_STEP.encode()),
                ("steps/l1.py", ALLOWED_STEP.encode()),
            ]
        )
    )
    assert {"steps/01.py", "steps/O1.py", "steps/l1.py"} <= set(package.entries)


def test_a_symlink_entry_is_refused():
    """Writing it would put a link into the catalog pointing anywhere on this
    machine, and the next thing to read that path would follow it."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as out:
        out.writestr("cls.json", json.dumps(META).encode())
        info = zipfile.ZipInfo("steps/01.py", FIXED_DATE_TIME)
        info.external_attr = 0o120777 << 16  # S_IFLNK
        out.writestr(info, "/etc/passwd")

    with pytest.raises(ClsError, match="symlink"):
        read_cls(buffer.getvalue())


def test_an_entry_whose_name_disagrees_with_the_directory_is_refused():
    """CPython lets an archive override an entry's name through the 0x7075
    extra field. An entry can then be checked as one file and unpack as
    another — the check sees `artifacts/01.png`, the disk gets `steps/01.py`.
    """
    real = "steps/01.py"
    disguise = b"artifacts/01.png"
    body = struct.pack("<BI", 1, zlib.crc32(real.encode())) + disguise

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as out:
        out.writestr("cls.json", json.dumps(META).encode())
        info = zipfile.ZipInfo(real, FIXED_DATE_TIME)
        info.extra = struct.pack("<HH", 0x7075, len(body)) + body
        out.writestr(info, ALLOWED_STEP)

    with pytest.raises(ClsError):
        read_cls(buffer.getvalue())


@pytest.mark.parametrize(
    ("filler", "count", "suffix"),
    [
        ("\U0001f600", 64, ""),
        ("가", 86, ""),
        ("é", 128, ""),
        # A directory entry, checked before it is skipped as one. Were it skipped
        # first, an over-long name would reach the disk carrying the content of
        # everything written under it.
        ("가", 86, "/"),
        ("가", 86, "/inner.bin"),
    ],
    ids=["emoji", "hangul", "e-acute", "directory-entry", "not-the-last-segment"],
)
def test_a_name_segment_longer_in_bytes_than_a_filesystem_holds_is_refused(filler, count, suffix):
    """A name inside the character limit whose segments are not inside the byte one.

    A filesystem measures one segment of a path at a time, and it measures it in
    bytes. Counting the whole name in characters passes a name that cannot be
    written: the write then fails partway through unpacking, as this machine
    breaking rather than as the package being wrong — which is the one thing
    its holder could act on. Every case here is well inside 256 characters.
    """
    payload = archive(
        [
            ("cls.json", json.dumps(META, sort_keys=True).encode()),
            ("steps/01.py", ALLOWED_STEP.encode()),
            (f"artifacts/{filler * count}{suffix}", b"solid\n"),
        ]
    )

    with pytest.raises(ClsError, match="bytes"):
        read_cls(payload)


def test_a_name_segment_that_only_just_fits_is_still_opened():
    """Guards the guard: an off-by-one here would refuse a writable name."""
    name = "artifacts/" + "가" * 85  # 255 bytes, the most a segment may be

    package = read_cls(
        archive(
            [
                ("cls.json", json.dumps(META, sort_keys=True).encode()),
                ("steps/01.py", ALLOWED_STEP.encode()),
                (name, b"solid\n"),
            ]
        )
    )

    assert name in package.entries


def test_a_long_name_split_into_writable_segments_is_accepted():
    """The rule is per segment, not per name. A deep path whose every segment
    fits is one the filesystem can hold, however long the whole of it reads."""
    name = "artifacts/" + "/".join(["가" * 85] * 2)

    package = read_cls(
        archive(
            [
                ("cls.json", json.dumps(META, sort_keys=True).encode()),
                ("steps/01.py", ALLOWED_STEP.encode()),
                (name, b"solid\n"),
            ]
        )
    )

    assert name in package.entries


@pytest.mark.parametrize("sites", [1, 2], ids=["local-header-only", "both-headers"])
def test_a_name_that_is_not_the_utf8_it_says_it_is_is_refused(sites):
    """An archive can flag a name as UTF-8 and then not be.

    `zipfile` decodes such a name with a bare `.decode("utf-8")`, so this raises
    out of reading the archive rather than out of any check here — and a reader
    that lets it past reports the package's fault as something this machine did.

    A name is written twice, in the local header and again in the central
    directory, and `zipfile` decodes each in a different place: the directory as
    it opens, the local header only when that member is read. Both are the
    package being wrong, so corrupting either one has to refuse — and covering
    only the case where both are corrupt would leave the second site untested,
    since fixing the first stops the archive ever being opened.
    """
    sound_bytes = archive(
        [
            ("cls.json", json.dumps(META, sort_keys=True).encode()),
            ("steps/é1.py", ALLOWED_STEP.encode()),
        ]
    )
    # The same length, so every offset the archive records still lands: only
    # the two bytes that spell 'é' become two that spell nothing.
    broken = sound_bytes.replace("steps/é1.py".encode(), b"steps/\xff\xfe1.py", sites)
    assert len(broken) == len(sound_bytes)

    with pytest.raises(ClsError):
        read_cls(broken)


# --- members that cannot be read ------------------------------------------

BULKY = b"solid\n" * 200


def with_a_bulky_artifact(compress: int = zipfile.ZIP_DEFLATED) -> bytes:
    """A sound package whose last entry is long enough to hold a real stream."""
    return archive(
        [
            ("cls.json", json.dumps(META, sort_keys=True).encode()),
            ("steps/01.py", ALLOWED_STEP.encode()),
            ("artifacts/01/model.stl", BULKY),
        ],
        compress=compress,
    )


@pytest.mark.parametrize(
    "method", [6, 12, 93, 99], ids=["implode", "bzip2", "zstandard", "winzip-aes"]
)
def test_a_member_compressed_with_a_method_the_format_does_not_use_is_refused(method):
    """`zipfile` raises out of *opening* such a member rather than out of any
    rule here: `NotImplementedError` for a method it cannot decompress at all,
    and an `OSError` from the decompressor for one it will attempt and finds the
    bytes are not. Neither is a `BadZipFile`, so both travel past the reader's
    own refusals and reach the app as this machine having failed — which is the
    one thing the package's holder cannot act on.
    """
    payload = rewritten(
        with_a_bulky_artifact(zipfile.ZIP_STORED), "artifacts/01/model.stl", method=method
    )

    # Anchored at the start, and naming the entry: the refusal wrapped around
    # opening the archive reads "this is not a readable package: That
    # compression method is not supported", so a pattern matched anywhere would
    # be satisfied by that too — and this test would pass with the check it
    # exists to pin deleted outright.
    with pytest.raises(ClsError, match=r"^'artifacts/01/model\.stl' is compressed with "):
        read_cls(payload)


@pytest.mark.parametrize(
    ("flag", "says"),
    [
        (0x1, r"^'artifacts/01/model\.stl' is encrypted,"),
        (0x20, r"^'artifacts/01/model\.stl' is stored as a patch"),
        (0x40, r"^'artifacts/01/model\.stl' is encrypted,"),
    ],
    ids=["encrypted", "compressed-patch", "strong-encryption"],
)
def test_a_member_that_is_encrypted_or_patched_is_refused(flag, says):
    """Three general purpose bits `zipfile` refuses to read a member under: one
    with a `RuntimeError` asking for a password, two with a `NotImplementedError`.
    A package carrying any of them is one this reader cannot open, and saying so
    is what lets whoever holds it repack without the bit set.
    """
    payload = rewritten(
        with_a_bulky_artifact(zipfile.ZIP_STORED), "artifacts/01/model.stl", flag=flag
    )

    with pytest.raises(ClsError, match=says):
        read_cls(payload)


def test_a_member_whose_bytes_are_not_the_deflate_they_claim_is_refused():
    """The one case the checks above cannot catch: the method and the flags are
    both what the format allows, and only decompressing the bytes finds out. A
    `zlib.error` is the package being wrong just as much as a declared method
    this reader does not have, so it is refused the same way.
    """
    payload = with_a_scrambled_stream(with_a_bulky_artifact(), "artifacts/01/model.stl")

    with pytest.raises(ClsError, match="decompress"):
        read_cls(payload)


@pytest.mark.parametrize(
    "compress", [zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED], ids=["stored", "deflated"]
)
def test_a_member_stored_the_way_the_format_allows_is_still_read(compress):
    """Guards the guard. Our own packer writes deflate, but an archive written
    by anything else may store its entries uncompressed, and a check that
    admitted only what we write would turn a sound package away.
    """
    package = read_cls(with_a_bulky_artifact(compress))

    assert package.entries["artifacts/01/model.stl"] == BULKY


def test_a_directory_entry_is_not_asked_how_it_would_be_opened():
    """Guards where the check sits. A directory entry is skipped before any
    member is read, so what it declares about compression is never acted on —
    and refusing it would turn away a package that reads perfectly well.
    """
    payload = rewritten(
        archive(
            [
                ("cls.json", json.dumps(META, sort_keys=True).encode()),
                ("steps/01.py", ALLOWED_STEP.encode()),
                ("artifacts/01/", b""),
                ("artifacts/01/model.stl", b"solid\n"),
            ]
        ),
        "artifacts/01/",
        method=99,
    )

    package = read_cls(payload)

    assert package.entries["artifacts/01/model.stl"] == b"solid\n"


def test_a_package_declaring_a_format_version_this_reader_has_not_got_is_refused():
    """`zipfile` will not build a directory entry claiming a zip version past the
    one it implements, and it says so with `NotImplementedError` — out of opening
    the archive, before any rule here is reached. That is the package being
    wrong, and it used to arrive as this machine having failed.
    """
    payload = bytearray(sound())
    payload[records(bytes(payload), "steps/01.py")[1] + CENTRAL_EXTRACT_VERSION] = (
        zipfile.MAX_EXTRACT_VERSION + 1
    )

    with pytest.raises(ClsError, match="not a readable package"):
        read_cls(bytes(payload))


def test_a_member_whose_data_would_begin_before_the_package_is_refused():
    """Every entry's recorded position is read relative to where the directory
    says it begins, so an archive that moves that by one moves every entry with
    it — and the first then points before the file. `zipfile` asks the buffer to
    seek there and gets a `ValueError`, out of reading a member.
    """
    payload = bytearray(sound())
    at = payload.rfind(END_RECORD) + END_DIRECTORY_OFFSET
    declared = int.from_bytes(payload[at : at + 4], "little")
    payload[at : at + 4] = (declared + 1).to_bytes(4, "little")

    with pytest.raises(ClsError, match="does not lie where"):
        read_cls(bytes(payload))


def test_a_member_whose_data_would_begin_past_the_package_is_refused():
    """The other end of the same fault, and it takes two edits to reach.

    An entry whose data would start past the end is normally caught as one
    overlapping the entry after it — so the last entry's record is first pointed
    somewhere far beyond the file, which leaves the entry before it with no
    neighbour to be measured against. That one's extra field is then widened
    until its data would begin past the end, and `zipfile` reads nothing where
    the bytes should be: a bare `EOFError`, carrying no message at all.
    """
    payload = bytearray(sound())
    last = records(bytes(payload), "artifacts/01/model.stl")[1] + CENTRAL_HEADER_OFFSET
    payload[last : last + 4] = (0x0FFFFFFF).to_bytes(4, "little")
    widened = records(bytes(payload), "steps/01.py")[0] + LOCAL_EXTRA_LENGTH
    payload[widened : widened + 2] = (60_000).to_bytes(2, "little")

    with pytest.raises(ClsError, match="does not lie where"):
        read_cls(bytes(payload))


# --- what an archive may expand into --------------------------------------


def test_an_archive_that_expands_far_beyond_its_size_is_refused():
    """A zip bomb. The reader holds entries in memory before anything is
    checked, so the cap has to bound what is read, not what the archive says
    about itself — the declared sizes are written by whoever built it.
    """
    payload = archive(
        [
            ("cls.json", json.dumps(META).encode()),
            ("steps/01.py", ALLOWED_STEP.encode()),
            ("artifacts/01/model.stl", b"\0" * (4 * 1024 * 1024)),
        ]
    )
    assert len(payload) < 64 * 1024  # compresses to almost nothing

    # Anchored at the start of the message, not matched anywhere inside it: the
    # refusal is raised inside the block that maps a member's read failures, and
    # a catch there wide enough to take this one too would still leave the words
    # below in the wrapper's text — passing while reporting a zip bomb as bytes
    # that would not decompress.
    with pytest.raises(ClsError, match=r"^this package expands"):
        read_cls(payload)


def test_an_archive_with_too_many_entries_is_refused():
    members = [("cls.json", json.dumps(META).encode())]
    members += [(f"artifacts/{n}.bin", b"x") for n in range(2100)]

    with pytest.raises(ClsError, match="entries"):
        read_cls(archive(members))


def test_a_directory_too_large_to_be_ours_is_refused_before_it_is_parsed(monkeypatch):
    """The cap has to land before the work it is there to prevent.

    Opening a `ZipFile` builds a `ZipInfo` for every record in the central
    directory, so a limit checked afterwards has already paid for the archive
    it is about to refuse — the bytes an attacker sends buy several times their
    own size in objects, on a route that takes no credentials.
    """
    # Enough records to overrun the ceiling, counted from the ceiling itself so
    # this keeps testing what it says when the limits move.
    per_record = CENTRAL_DIRECTORY_RECORD_BYTES + len("artifacts/000000.bin")
    members = [("cls.json", json.dumps(META).encode())]
    members += [
        (f"artifacts/{n:06d}.bin", b"x")
        for n in range(MAX_CENTRAL_DIRECTORY_BYTES // per_record + 1_000)
    ]
    payload = archive(members)

    opened = []
    unpatched = zipfile.ZipFile

    def spy(*args, **kwargs):
        opened.append(args)
        return unpatched(*args, **kwargs)

    monkeypatch.setattr(zipfile, "ZipFile", spy)

    with pytest.raises(ClsError, match="entries"):
        read_cls(payload)

    assert not opened, "the central directory was parsed before the limit refused it"


def test_a_directory_size_spelling_the_signature_does_not_hide_it():
    """The record's own fields can spell the signature the search looks for.

    A declared directory size of 0x06054B50 puts `PK\\x05\\x06` inside the very
    record being read, so a check that only searched backwards would find that
    one, fail to fit a record around it, and wave the archive through to the
    reader it was meant to protect. The number is one an archive is free to
    declare, so it is a way to aim the check at the wrong bytes.
    """
    end_record = struct.pack(
        "<4s4H2LH",
        b"PK\x05\x06",
        0,  # this disk
        0,  # disk the directory starts on
        0xFFFF,  # entries on this disk
        0xFFFF,  # entries in total
        0x06054B50,  # directory size — the signature, as a number
        0,  # directory offset
        0,  # no comment, so the record sits exactly at the end
    )

    with pytest.raises(ClsError, match="entries"):
        read_cls(end_record)


def test_a_zip64_record_cannot_declare_a_size_the_check_does_not_see():
    """`zipfile` prefers the zip64 record whenever it is there.

    It overwrites the size it has already read without asking whether the
    32-bit field was saturated, so an archive can carry a small number where a
    check might look and the real one where the reader looks. Both have to read
    the same record or the limit is only advisory.
    """
    zip64_record = struct.pack(
        "<4sQ2H2L4Q",
        b"PK\x06\x06",
        ZIP64_EOCD_BYTES - 12,  # size of this record, excluding the first 12 bytes
        45,  # made by
        45,  # needed to extract
        0,  # this disk
        0,  # disk the directory starts on
        1,  # entries on this disk
        1,  # entries in total
        1 << 40,  # directory size — the one that counts
        0,  # directory offset
    )
    locator = struct.pack("<4sLQL", b"PK\x06\x07", 0, 0, 1)
    end_record = struct.pack(
        "<4s4H2LH",
        b"PK\x05\x06",
        0,
        0,
        1,
        1,
        100,  # a directory size small enough to pass, and never used
        0,
        0,
    )

    with pytest.raises(ClsError, match="entries"):
        read_cls(zip64_record + locator + end_record)


def _relocated_zip64(size: int, *, plain_size: int = 100) -> bytes:
    """A zip64 record somewhere other than just behind its locator.

    Older readers take this record from a fixed distance behind the locator;
    since gh-139700 — backported into 3.12.12, 3.13.10 and their contemporaries
    — the reader seeks to the offset the locator declares. An archive that puts
    a record in one place and nothing in the other is read differently by the
    two, so both have to be looked at.
    """
    record = struct.pack(
        "<4sQ2H2L4Q", b"PK\x06\x06", ZIP64_EOCD_BYTES - 12, 45, 45, 0, 0, 1, 1, size, 0
    )
    locator = struct.pack("<4sLQL", b"PK\x06\x07", 0, 0, 1)  # declares offset 0
    end_record = struct.pack("<4s4H2LH", b"PK\x05\x06", 0, 0, 1, 1, plain_size, 0, 0)
    # Nothing at the fixed place the older readers look, so only the declared
    # offset leads anywhere.
    return record + b"\0" * ZIP64_EOCD_BYTES + locator + end_record


def _zip64_off_the_front(size: int) -> bytes:
    """A file too short for the record the locator points behind it.

    Seeking past the start of a `BytesIO` lands on byte zero rather than
    failing, so a reader reaching that far back is handed the beginning of the
    file — and reads whatever is there as the zip64 record. Skipping the
    candidate for being off the front would look at a different record than the
    reader does.
    """
    record = struct.pack(
        "<4sQ2H2L4Q", b"PK\x06\x06", ZIP64_EOCD_BYTES - 12, 45, 45, 0, 0, 1, 1, size, 0
    )
    locator = struct.pack("<4sLQL", b"PK\x06\x07", 0, 0xFFFFFFFF, 1)
    end_record = struct.pack("<4s4H2LH", b"PK\x05\x06", 0, 0, 1, 1, 100, 0, 0)
    # One byte short of a whole record, so the fixed slot lands before byte zero.
    return record[:-1] + locator + end_record


def test_a_zip64_record_is_found_wherever_a_reader_would_look_for_it():
    with pytest.raises(ClsError, match="entries"):
        read_cls(_relocated_zip64(1 << 40))


def test_a_small_zip64_record_does_not_hide_a_large_plain_one():
    """The zip64 record is not automatically the answer — only a candidate.

    A reader that finds nothing at the fixed place falls back to the 32-bit
    field, so a decoy zip64 record at the declared offset saying almost nothing
    would cover for a plain size that says a great deal. Every candidate has to
    be weighed, not the first one that turns up.
    """
    with pytest.raises(ClsError, match="entries"):
        read_cls(_relocated_zip64(46, plain_size=MAX_CENTRAL_DIRECTORY_BYTES + 1))


def _behind_a_comment(payload: bytes, length: int) -> bytes:
    """The same archive with a trailing comment of ``length`` bytes.

    A comment is the only thing that puts the end record anywhere but flush
    against the end of the file, so it is the only way to reach the branch that
    searches backwards for it — and, once the comment is long enough that the
    archive outgrows the search window, the only way to reach it at a non-zero
    starting offset. Without one of these every payload starts its search at
    byte zero, where an offset that was never converted back to an absolute
    position happens to be the right answer anyway.
    """
    record = payload[-EOCD_BYTES:]
    assert record[:4] == b"PK\x05\x06", "expected an end record with no comment already"
    comment = b"#" * length
    return payload[:-EOCD_BYTES] + record[:-2] + struct.pack("<H", length) + comment


# One of these per way an end-of-directory record has been found: a sound
# archive, one sitting behind a comment long enough to push the search window
# off byte zero, zip64 in each of the two places a reader may look for it, a
# record whose own fields spell the signature again, and three that are not
# archives. Named once because two tests below have to cover exactly the same
# ground — the check must agree with `zipfile` on all of them, and must give the
# same answer whether it reads them from memory or off a file.
EVERY_END_RECORD_SHAPE = [
    sound(),
    _behind_a_comment(sound(), MAX_ZIP_COMMENT_BYTES),
    _behind_a_comment(sound(), 8),
    _relocated_zip64(1 << 40),
    _relocated_zip64(46, plain_size=MAX_CENTRAL_DIRECTORY_BYTES + 1),
    _zip64_off_the_front(1 << 40),
    struct.pack("<4s4H2LH", b"PK\x05\x06", 0, 0, 1, 1, 0x06054B50, 0, 0),
    # A signature close enough to the end that no whole record can follow it.
    # The search finds something and there is still nothing to read, which is a
    # different refusal from finding nothing at all.
    b"\x00" * 20 + b"PK\x05\x06" + b"\x00" * 6,
    b"",
    b"PK\x05\x06",
    b"not an archive at all",
]


@pytest.mark.parametrize("payload", EVERY_END_RECORD_SHAPE)
def test_the_size_checked_is_never_smaller_than_the_size_read(payload):
    """The limit is only as good as this agreement.

    `zipfile` decides which record describes the archive, and the check ahead of
    it has to reach for the same one — or at least never a smaller answer, since
    reading low is how a bound gets walked past. The private lookup is named on
    purpose: this coupling is the security property, so it should fail loudly on
    a Python that changes it rather than go quiet.

    A lookup that raises counts as a pass: `zipfile` then opens nothing, so
    there is no reading for this one to come in under. Which of the shapes below
    raise depends on the patch release, so what this covers moves with the
    interpreter — deliberately, since it is the property that has to hold
    everywhere, not the route to it.

    The private names are read *before* the try, and only what an archive can
    provoke is caught. Resolving them inside it would let this test pass green
    on the one Python it exists to catch: the one where the lookup is gone.

    Both sides are handed the same kind of thing — an open file — because that
    is what `zipfile` has always taken, and reading the two through one
    interface is what makes "the same record" a claim about the same reads.
    """
    end_record_data = zipfile._EndRecData
    directory_size = zipfile._ECD_SIZE

    try:
        record = end_record_data(io.BytesIO(payload))
    except (zipfile.BadZipFile, OSError, struct.error, ValueError):
        return
    if record is None:
        return

    ours = _declared_directory_size(io.BytesIO(payload))
    assert ours is not None
    assert ours >= record[directory_size]


@pytest.mark.parametrize("payload", EVERY_END_RECORD_SHAPE)
def test_a_package_read_off_a_file_is_measured_as_one_held_in_memory(payload, tmp_path):
    """The bound has to be the same one wherever the package is sitting.

    A body arriving over the wire is written to a file rather than assembled in
    memory, so this check now runs against something it can only seek around
    instead of something it can slice. Reading low is how a bound gets walked
    past, and a file that answered differently from a buffer would be exactly
    that — for the one route the size limit exists to protect.
    """
    on_disk = tmp_path / "package.cls"
    on_disk.write_bytes(payload)

    with on_disk.open("rb") as fp:
        assert _declared_directory_size(fp) == _declared_directory_size(io.BytesIO(payload))


@pytest.mark.parametrize("length", [8, MAX_ZIP_COMMENT_BYTES])
def test_a_sound_package_behind_a_comment_is_still_opened(length):
    """A comment is legal, and the end record moves back by exactly its length.

    This is the false-positive guard for the search that finds it: a reader that
    located the record at the wrong offset would read the directory's size out
    of the comment instead, and refuse a package there is nothing wrong with.
    """
    package = read_cls(_behind_a_comment(sound(), length))

    assert set(package.entries) == {"cls.json", "steps/01.py", "artifacts/01/model.stl"}


class OneByteAtATime:
    """A reader that answers every read with a single byte, the way a pipe can.

    Files answer a read in full unless they are at the end, and every reader
    reaching this module today is one. The reader takes any binary file now,
    though, and a short answer taken at face value would measure the directory
    as smaller than it is — the only direction this bound must never move in.
    """

    def __init__(self, payload: bytes) -> None:
        self._inner = io.BytesIO(payload)

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        return self._inner.seek(offset, whence)

    def tell(self) -> int:
        return self._inner.tell()

    def read(self, size: int = -1) -> bytes:
        return self._inner.read(1 if size < 0 else min(1, size))


@pytest.mark.parametrize("payload", EVERY_END_RECORD_SHAPE)
def test_a_reader_that_answers_short_measures_the_same(payload):
    """Whatever the file hands back a read at a time, the size read is the size."""
    dribbled = _declared_directory_size(OneByteAtATime(payload))

    assert dribbled == _declared_directory_size(io.BytesIO(payload))


def test_a_package_opens_the_same_off_a_file_as_out_of_bytes(tmp_path):
    """What the reader returns must not depend on how the package was handed over."""
    payload = sound()
    on_disk = tmp_path / "package.cls"
    on_disk.write_bytes(payload)

    with on_disk.open("rb") as fp:
        from_file = read_cls(fp)
    from_bytes = read_cls(payload)

    assert from_file.canonical_digest == from_bytes.canonical_digest
    assert from_file.entries == from_bytes.entries


@pytest.mark.parametrize("filler", ["a", "가", "é", "\U0001f600"])
def test_a_package_at_every_limit_at_once_is_still_opened(filler):
    """The directory check is sized off the rules, so the largest package the
    rules allow has to pass it — as many entries as may be carried, each with a
    name as long as one may be. A cheaper check that guessed at a typical name
    would refuse this, and refuse it for a reason the format does not state.

    Names are bounded in characters and stored as UTF-8, so the non-ASCII cases
    are the ones that catch a bound counted in the wrong unit. The widest is the
    emoji: 256 characters across five parts, 973 bytes.
    """
    members = [("cls.json", json.dumps(META).encode())]
    for n in range(MAX_ENTRIES - 1):
        members.append((at_the_name_limit(f"artifacts/{n:04d}", filler), b"x"))
    payload = archive(members)

    package = read_cls(payload)

    assert len(package.entries) == MAX_ENTRIES
    # Guards the guard: a helper that quietly stopped short would leave this
    # passing on names smaller than the ones it exists to size for.
    assert {len(name) for name in package.entries} == {len("cls.json"), MAX_ENTRY_NAME_LENGTH}


# --- the metadata ---------------------------------------------------------


def test_a_package_with_no_metadata_is_refused():
    with pytest.raises(ClsError, match="cls.json"):
        read_cls(sound(meta=None))


def test_metadata_that_is_not_json_is_refused():
    with pytest.raises(ClsError, match="cls.json"):
        read_cls(archive([("cls.json", b"{not json"), ("steps/01.py", ALLOWED_STEP.encode())]))


@pytest.mark.parametrize("missing", ["format_version", "content_version", "license"])
def test_metadata_missing_a_required_field_is_refused(missing):
    meta = {key: value for key, value in META.items() if key != missing}

    with pytest.raises(ClsError, match=missing):
        read_cls(sound(meta=meta))


def test_a_format_version_from_the_future_is_refused():
    """Reading it with this version's rules would be guessing at what the newer
    format means."""
    with pytest.raises(ClsError, match="format_version"):
        read_cls(sound(meta={**META, "format_version": 2}))


def test_metadata_keys_we_do_not_know_are_kept():
    """The format may grow fields this version has no opinion about, and they
    are inside the digest — dropping them would lose data the publisher signed
    up to ship."""
    package = read_cls(sound(meta={**META, "written_by_a_later_version": "keep me"}))

    assert package.meta["written_by_a_later_version"] == "keep me"


# --- the code gate --------------------------------------------------------


def test_step_code_that_the_gate_refuses_is_named_with_its_reasons():
    """This is the check that matters: an upload gate ran once somewhere else,
    and this package may never have been through it."""
    package = read_cls(
        archive(
            [
                ("cls.json", json.dumps(META).encode()),
                ("steps/01.py", ALLOWED_STEP.encode()),
                ("steps/02.py", BANNED_STEP.encode()),
            ]
        )
    )

    failures = verify_steps(package)

    assert [failure.path for failure in failures] == ["steps/02.py"]
    assert failures[0].reasons


def test_step_code_the_gate_accepts_reports_nothing():
    assert verify_steps(read_cls(sound())) == []


def test_step_code_that_is_not_utf8_is_refused():
    """The gate reads text. A step it cannot decode is one it cannot check,
    which is not the same as one that passed."""
    with pytest.raises(ClsError, match="utf-8"):
        read_cls(archive([("cls.json", json.dumps(META).encode()), ("steps/01.py", b"\xff\xfe")]))


def test_every_file_under_steps_is_checked_whatever_it_is_called():
    """Selecting on a lowercase `.py` suffix would let `02.PY` through on a
    case-insensitive filesystem, where a runner globbing `steps/*.py` still
    picks it up."""
    package = read_cls(
        archive(
            [
                ("cls.json", json.dumps(META).encode()),
                ("steps/01.py", ALLOWED_STEP.encode()),
                ("steps/02.PY", BANNED_STEP.encode()),
                ("steps/03", BANNED_STEP.encode()),
            ]
        )
    )

    assert {failure.path for failure in verify_steps(package)} == {"steps/02.PY", "steps/03"}
