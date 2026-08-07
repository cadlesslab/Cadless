"""Read a `.cls` container, and define what one is.

The read half, plus the vocabulary a writer needs: the layout the format
allows, the refusal types, the entry-name rule, and the canonical digest.
This build reads packages and does not assemble them; a build that writes one
imports those definitions from here.

Split this way because of who depends on which: core import reads a package —
`cadless.catalog.importer` reaches straight into this module — while only a
publishing build assembles one. Everything reaching this side is written by
someone else, so it is the side that has to refuse rather than repair.

The digest below is the reason the container is written to a specification
rather than to whatever a packer happens to produce. Whoever receives a package
recomputes it from the bytes that arrived and stores that value as the
package's identity, so an implementation that is merely self-consistent would
hand one over fine and disagree about what it handed over.
"""

from __future__ import annotations

import hashlib
import io
import json
import stat
import unicodedata
import zipfile
import zlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, BinaryIO

from cadless.validation import validate_code

# Excluded from the digest so a signature can be taken over the digest and then
# added to the package without changing the value it signs.
DIGEST_EXCLUDED = frozenset({"checksums", "signature"})

META_NAME = "cls.json"
SUPPORTED_FORMAT_VERSIONS = frozenset({1})

STEPS_PREFIX = "steps/"
# The whole layout. Everything stored is written to disk and shipped onward, so
# an unrecognised entry is refused rather than ignored — otherwise it travels by
# not being understood, and the code gate, which looks under `steps/`, never
# sees it.
ALLOWED_TOP_LEVEL = frozenset({META_NAME, "checksums", "signature"})
ALLOWED_PREFIXES = (STEPS_PREFIX, "artifacts/", "transcript/")

# General purpose bit 11. A non-ASCII name without it is decoded as cp437 by
# CPython and kept as raw bytes by other readers, so the two disagree about what
# the package contains — and so would the digest.
UTF8_NAME_FLAG = 0x800

# How a member may be stored. Reading one takes an implementation of the method
# it declares, and `zipfile` raises rather than returning when it has none: a
# `NotImplementedError` for a method it cannot decompress at all, and, for one it
# will attempt, an `OSError` from the decompressor when the bytes turn out not to
# be that either. Neither is a `BadZipFile`, so both travel past every refusal
# here and land as this machine failing on a package that is simply not one this
# format defines.
#
# Our packer writes deflate and nothing else. Stored is admitted alongside it
# because an archive written by anything else may hold its entries uncompressed,
# and that is a package this reader reads perfectly well — a rule admitting only
# what we write would turn away a sound one.
SUPPORTED_COMPRESSION = frozenset({zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED})

# General purpose bits under which `zipfile` will not read a member at all: bit 0
# asks for a password, bit 5 says the content is a patch rather than the file,
# and bit 6 a form of encryption it does not implement.
#
# Written as what is refused rather than what is allowed. Bit 11 above is
# required of a non-ASCII name, and bit 3 says the sizes follow the content
# instead of preceding it, which anything writing an archive in a single pass
# sets legitimately — a rule listing what may be set would refuse both.
ENCRYPTED_FLAG = 0x1
COMPRESSED_PATCH_FLAG = 0x20
STRONG_ENCRYPTION_FLAG = 0x40
UNREADABLE_MEMBER_FLAGS = ENCRYPTED_FLAG | COMPRESSED_PATCH_FLAG | STRONG_ENCRYPTION_FLAG

# What an archive may expand into. The ratio applies to the bytes actually
# received: sizes an archive declares about itself are written by whoever built
# it, so a cap derived from them only ever agrees with them.
MAX_ENTRIES = 2_000
MAX_UNCOMPRESSED_BYTES = 128 * 1024 * 1024
MAX_EXPANSION_RATIO = 200
READ_CHUNK_BYTES = 1024 * 1024

# The manifest has no entry of its own in the layout the format allows, and
# only its ``tags``, ``domain`` and ``category`` have a home among the keys that
# layout defines. It rides in ``cls.json`` under a name of our own instead:
# unknown keys there are kept and hashed, so nothing is lost and nothing can be
# edited unnoticed. The three that do have a home are stated in both places —
# the format's keys classify the listing, and this copy rebuilds the item. The
# two are not always the same string: what is stated at the top level is
# normalised for the comparison a filter makes, while this copy stays as the
# author wrote it, because an item is rebuilt from it.
MANIFEST_KEY = "cadless_manifest"


# A receiver refuses a name longer than this, or one that is not a plain
# relative POSIX path. Refusing here names the offending file; refusing there
# fails the upload after the bytes have been sent.
MAX_ENTRY_NAME_LENGTH = 256

# What one segment of that name may be, in the unit a filesystem counts. The
# bound above is the whole name in characters; a filesystem bounds each segment
# between the separators, and it counts bytes. Both numbers were measured rather
# than assumed, and they do not agree: Debian on overlayfs — what the deployment
# image runs — stops at 255 bytes, which is 85 Hangul characters, while macOS
# stops at 255 UTF-16 code units, which is 255 of them. No character is fewer
# bytes in UTF-8 than it is code units in UTF-16, so a segment inside this bound
# is inside both — including after the decomposition HFS+ normalises to, where
# the worst case is a three-byte syllable becoming three units.
#
# Without it a name of 256 characters carries a segment of nearly a thousand
# bytes and passes: the write is then what refuses it, halfway through
# unpacking, as this machine failing rather than as the package being wrong —
# and on one machine and not another, since the two count differently.
MAX_ENTRY_SEGMENT_BYTES = 255

# What the entries a package may carry can take up in its central directory: a
# record is 46 bytes plus the name, and a longer name is refused above. Checked
# before the archive is opened, because opening one builds a ``ZipInfo`` for
# every record in it — several times the received bytes in objects, spent
# *before* ``MAX_ENTRIES`` gets a chance to say no. The slack is for the extra
# and comment fields, which our packer does not write and another might.
#
# The name is bounded in characters and stored as UTF-8, so the room it needs is
# four bytes a character — a bound counted in characters would refuse a package
# of legal names that happen not to be ASCII, which is most of them in some
# languages. The per-part bound does not lower that worst case: a name of
# four-byte characters stays legal by splitting into parts, so the widest name
# still approaches this.
CENTRAL_DIRECTORY_RECORD_BYTES = 46
CENTRAL_DIRECTORY_SLACK_BYTES = 64
MAX_ENTRY_NAME_BYTES = MAX_ENTRY_NAME_LENGTH * 4
MAX_CENTRAL_DIRECTORY_BYTES = MAX_ENTRIES * (
    CENTRAL_DIRECTORY_RECORD_BYTES + MAX_ENTRY_NAME_BYTES + CENTRAL_DIRECTORY_SLACK_BYTES
)

# The end of central directory record: a fixed 22 bytes, last in the file except
# for a comment of up to 0xFFFF. When a value in it will not fit, a zip64 record
# holding the real one sits just ahead of it, behind a 20-byte locator.
EOCD_SIGNATURE = b"PK\x05\x06"
EOCD_BYTES = 22
EOCD_DIRECTORY_SIZE_OFFSET = 12
MAX_ZIP_COMMENT_BYTES = 0xFFFF
ZIP64_EOCD_SIGNATURE = b"PK\x06\x06"
ZIP64_EOCD_BYTES = 56
ZIP64_EOCD_DIRECTORY_SIZE_OFFSET = 40
ZIP64_LOCATOR_SIGNATURE = b"PK\x06\x07"
ZIP64_LOCATOR_BYTES = 20
ZIP64_LOCATOR_OFFSET_FIELD = 8


class PackError(RuntimeError):
    """The item cannot be packed: something the container requires is missing."""


class ClsError(RuntimeError):
    """The package is not one this version can read, so it is not opened.

    Everything reaching the reader is written by someone else — the archive,
    the entry names, and every value in ``cls.json`` — and the code inside runs
    on this machine afterwards. A package that is not exactly what the format
    allows is refused rather than repaired: guessing at what a malformed one
    meant is how the guessing gets exploited.
    """


@dataclass(frozen=True)
class ClsPackage:
    """A `.cls` that has been read and found well-formed."""

    entries: Mapping[str, bytes]
    meta: Mapping[str, Any]
    canonical_digest: str

    @property
    def manifest(self) -> Mapping[str, Any]:
        """The catalog manifest the package carries, or an empty mapping."""
        carried = self.meta.get(MANIFEST_KEY)
        return carried if isinstance(carried, dict) else {}

    def steps(self) -> dict[str, str]:
        """Every step source. Decoding already succeeded when this was opened."""
        return _decode_steps(self.entries)


@dataclass(frozen=True)
class StepRefusal:
    """One step the code gate would not pass, and why."""

    path: str
    reasons: tuple[str, ...]


def canonical_digest(entries: Mapping[str, bytes]) -> str:
    """Hash the path-sorted manifest of per-entry hashes.

    Each line is ``{sha256}:{path byte length}:{path}\\n``. The length prefix is
    what makes the encoding injective: without it an entry *name* containing a
    newline can render as an additional manifest line, and one package hashes
    identically to a different package with one more entry.

    Sorting is by the entry path, not by the rendered line: the ordering must
    not depend on the content hashes it carries. The key encodes first because
    that is what a receiver sorts, and saying so here means a future name
    normalisation cannot quietly change the order on one side only.
    """
    included = sorted(
        ((name, blob) for name, blob in entries.items() if name not in DIGEST_EXCLUDED),
        key=lambda item: item[0].encode("utf-8"),
    )
    lines = []
    for name, blob in included:
        raw = name.encode("utf-8")
        if not raw:
            # This function's whole job is injectivity; dropping an entry
            # quietly is the last thing it should do.
            raise ValueError("an entry name may not be empty")
        lines.append(b"%s:%d:%s\n" % (hashlib.sha256(blob).hexdigest().encode(), len(raw), raw))
    return hashlib.sha256(b"".join(lines)).hexdigest()


def digest_of(payload: bytes) -> str:
    """The canonical digest of an already-packed `.cls`.

    Read back out of the container rather than remembered from the way in, so
    what is compared against a receiver's value is what the bytes actually
    say — the same thing that receiver reads.
    """
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        return canonical_digest(
            {info.filename: archive.read(info) for info in archive.infolist() if not info.is_dir()}
        )


def safe_entry_name(name: str) -> str:
    """Return ``name`` unchanged, or say which file cannot travel.

    These are the format's rules, and one of them is the filesystem's. The
    rest cost the upload if they are left to be checked there; that one costs a
    write that fails partway through, on some machines and not others.
    """
    segments = name.split("/")
    if len(name) > MAX_ENTRY_NAME_LENGTH:
        raise PackError(f"{name!r} is longer than {MAX_ENTRY_NAME_LENGTH} characters")
    for segment in segments:
        # Each segment on its own, and in bytes: a name inside the limit above
        # can still hold one no filesystem will take.
        if len(segment.encode()) > MAX_ENTRY_SEGMENT_BYTES:
            raise PackError(
                f"{name!r} has a segment of {len(segment.encode())} bytes; no segment of "
                f"a name may be over {MAX_ENTRY_SEGMENT_BYTES}"
            )
    if any(character < "\x20" or character == "\x7f" for character in name):
        raise PackError(f"{name!r} contains a control character")
    if "\\" in name or ":" in name or name.startswith("/"):
        raise PackError(f"{name!r} is not a relative POSIX path")
    if any(segment in ("", ".", "..") for segment in segments):
        raise PackError(f"{name!r} contains an empty or traversal segment")
    return name


def read_cls(source: bytes | BinaryIO, *, expected_digest: str | None = None) -> ClsPackage:
    """Open a `.cls` written by someone else, or refuse it.

    ``expected_digest`` is what the publisher recorded when the package was
    published. Checking it here is the only thing that notices an edit made
    afterwards — the upload gate saw the package once, and a package delivered
    by any other route was never seen at all.

    An open file is accepted as well as bytes so that a package arriving over
    the wire never has to be held in memory whole. Bytes are wrapped rather than
    copied — `BytesIO` shares an immutable buffer — so the callers that already
    had the package in hand pay nothing for the widening. The file is left open:
    whoever opened it closes it, and `zipfile` does not close what it was handed.
    """
    fp = io.BytesIO(source) if isinstance(source, (bytes, bytearray)) else source

    _reject_oversized_directory(fp)
    # Read before the archive is opened, so the reader is never asked where it
    # is while it is working through the directory.
    budget = _budget_for(fp)
    # Both of those left the file wherever their last read stopped. `zipfile`
    # seeks for itself before it reads anything, so this is not what makes it
    # work — it is so that what it does is not the reason it works.
    fp.seek(0)

    try:
        archive = zipfile.ZipFile(fp)
        with archive:
            entries = _read_entries(archive, budget=budget)
    except (zipfile.BadZipFile, UnicodeDecodeError, NotImplementedError) as exc:
        # The decode is the second of those: an entry may flag its name as UTF-8
        # and `zipfile` takes it at its word, so a name that is not raises out of
        # reading rather than out of any rule here. Left out, that leaves the
        # module as this machine failing on a package that is simply wrong,
        # which is the one thing its holder could act on.
        #
        # Reading is inside this rather than after it because a name is written
        # twice — the local header and the central directory — and `zipfile`
        # decodes them in two places: the directory as the archive opens, the
        # local header only when that member is read. Guarding the open alone
        # leaves the second one raising past everything.
        #
        # The `NotImplementedError` is the third of them, and it comes from
        # building the directory rather than from reading anything: `zipfile`
        # refuses a record claiming a zip version past the one it implements,
        # which is again a package this build cannot read rather than a fault
        # here. Safe to catch alongside the others precisely because it is a
        # *sibling* of the refusals below and not an ancestor — both it and
        # `ClsError` derive from `RuntimeError`, so catching one leaves the
        # other untouched.
        #
        # The refusals below are `ClsError`, which is not caught here, so they
        # pass through as themselves. Widening this to `RuntimeError` would
        # swallow them — both `ClsError` and `PackError` derive from it.
        raise ClsError(f"this is not a readable package: {exc}") from exc

    _decode_steps(entries)

    digest = canonical_digest(entries)
    if expected_digest is not None and digest != expected_digest:
        raise ClsError(
            "this package does not match the digest it was published under — "
            f"expected {expected_digest}, got {digest}"
        )
    return ClsPackage(entries=entries, meta=_read_metadata(entries), canonical_digest=digest)


def _reject_oversized_directory(fp: BinaryIO) -> None:
    """Refuse a directory too large to be describing a package of ours.

    Opening the archive builds an object per record, so an archive of three
    hundred thousand empty entries costs six times its own size in memory to
    reject — over a route that takes no credentials, and one the body limit lets
    reach a hundred and twenty-eight megabytes.

    The size is used rather than the count the record also carries: that field is
    sixteen bits and saturates, and either way it is a number chosen by whoever
    built the archive, while `zipfile` reads records until the *bytes* run out.

    What this bounds is the memory, not the entry count. A record is 46 bytes
    plus its name, and the room reserved here assumes names near the longest one
    allowed, stored as UTF-8 — so an archive of nameless records fits about
    twenty-five times ``MAX_ENTRIES`` of them under the same ceiling, and pays
    for that many objects before :func:`_read_entries` counts them. That is a
    fixed price of a few tens of megabytes rather than one that grows with what
    is sent, which is the property worth having; the count itself is still
    enforced afterwards, where it can be read exactly.
    """
    size = _declared_directory_size(fp)
    if size is not None and size > MAX_CENTRAL_DIRECTORY_BYTES:
        raise ClsError(
            f"this package's directory of entries is {size} bytes, more than the "
            f"{MAX_CENTRAL_DIRECTORY_BYTES} that {MAX_ENTRIES} entries can occupy, "
            "so it was not opened"
        )


def _size_of(fp: BinaryIO) -> int:
    """How long the archive is, leaving it open where it was found."""
    here = fp.tell()
    try:
        return fp.seek(0, io.SEEK_END)
    finally:
        fp.seek(here)


def _at(fp: BinaryIO, offset: int, n: int) -> bytes:
    """``payload[offset : offset + n]``, read out of a file rather than sliced.

    Out of range answers empty instead of raising, which is what slicing did
    here before and what the callers are written against: every offset reaching
    this comes from a field the archive chose, and one of them is a full sixty
    four bits wide — a number `seek` would refuse outright rather than shrug at,
    turning a package this module means to refuse into a failure of its own.

    Read to length rather than in one call. A buffered file comes up short only
    at the end, and those are the only ones that reach here today — but the
    reader takes any `BinaryIO` now, and one that answered a partial read would
    make the directory measure *smaller* than it is. Short is the single
    direction this bound must never move in.
    """
    if offset < 0 or offset > _size_of(fp):
        return b""
    fp.seek(offset)
    got = fp.read(n)
    while len(got) < n and (more := fp.read(n - len(got))):
        got += more
    return got


def _declared_directory_size(fp: BinaryIO) -> int | None:
    """How many bytes the archive says its central directory takes, if it says.

    Read only to decide whether opening the archive is worth it, so anything
    unreadable here answers ``None`` and leaves the verdict to `zipfile`, which
    refuses a malformed archive by itself. The last matching record within a
    comment's reach is taken, which is the record CPython reads too — a check
    that read a *different* one could be talked past.
    """
    at = _end_record_at(fp)
    if at is None:
        return None
    start = at + EOCD_DIRECTORY_SIZE_OFFSET
    sizes = [int.from_bytes(_at(fp, start, 4), "little")]
    sizes.extend(_zip64_directory_sizes(fp, at))
    return max(sizes)


def _zip64_directory_sizes(fp: BinaryIO, at: int) -> list[int]:
    """Every size a zip64 record here could be read as giving.

    Such a record is looked for at all whenever its locator is present, rather
    than only when the smaller field is saturated: `zipfile` overwrites the size
    it has already read without asking whether the field it came from was full.

    Two places, because readers disagree about which one holds the record. It
    used to be read at a fixed distance behind the locator; since gh-139700 —
    which landed in 3.14 and was carried back into 3.12.12, 3.13.10 and their
    contemporaries, so the line falls between patch releases rather than
    versions — the reader seeks to the offset the locator *declares* and only
    falls back to the fixed place. An archive can therefore put a modest record
    where one build looks and the real one where another does.

    All of them are returned rather than one, and the caller takes the largest
    it has: this is a bound, and reading the smaller of several candidates is
    how a bound gets walked past.
    """
    locator = at - ZIP64_LOCATOR_BYTES
    if locator < 0 or _at(fp, locator, 4) != ZIP64_LOCATOR_SIGNATURE:
        return []
    start = locator + ZIP64_LOCATOR_OFFSET_FIELD
    declared = int.from_bytes(_at(fp, start, 8), "little")
    # Clamped rather than skipped when it runs off the front: seeking past the
    # start of a `BytesIO` lands on byte zero instead of failing, so a reader
    # asking for a record that far back is handed whatever begins the file.
    candidates = (declared, max(0, locator - ZIP64_EOCD_BYTES))
    return [size for record in candidates if (size := _zip64_size_at(fp, record)) is not None]


def _zip64_size_at(fp: BinaryIO, record: int) -> int | None:
    """The directory size a zip64 record holds, if one starts here at all."""
    if record < 0 or _at(fp, record, 4) != ZIP64_EOCD_SIGNATURE:
        return None
    start = record + ZIP64_EOCD_DIRECTORY_SIZE_OFFSET
    return int.from_bytes(_at(fp, start, 8), "little")


def _end_record_at(fp: BinaryIO) -> int | None:
    """Where the end of central directory record starts, found as CPython finds it.

    Two steps, because `zipfile` has two: a record with no comment sitting
    exactly at the end, and otherwise a search back through a comment's worth of
    bytes for the last one. Both are needed. Searching alone would read a
    different record than the reader that follows, because the record's own
    fields can spell the signature again — a size of 0x06054B50 is a number an
    archive is free to declare — and a check looking at a record nobody opens is
    worse than no check, since it can be aimed.
    """
    size = _size_of(fp)
    if size < EOCD_BYTES:
        return None
    unadorned = size - EOCD_BYTES
    if _at(fp, unadorned, 4) == EOCD_SIGNATURE and _at(fp, size - 2, 2) == b"\x00\x00":
        return unadorned
    # The search runs over the tail rather than the whole file, which is what
    # bounds this read: a comment is sixteen bits long at most, so the window is
    # a fixed sixty four kilobytes however large the package is.
    reach = max(0, size - (MAX_ZIP_COMMENT_BYTES + EOCD_BYTES))
    found = _at(fp, reach, size - reach).rfind(EOCD_SIGNATURE)
    if found < 0:
        # `zipfile` gives up here too, and refuses the archive on its own.
        return None
    at = reach + found
    if size - at < EOCD_BYTES:
        return None
    return at


def _folded(name: str) -> str:
    """The name as a filesystem that ignores case and composition sees it.

    macOS is the one that matters here — it is what this tool runs on, and its
    filesystem compares names with case and Unicode composition folded away, so
    two entries differing only in those are one file. Written in the order the
    directory lists them, the survivor takes its name from the first and its
    bytes from the last: both chosen by whoever built the archive, and the
    result is not what the digest was taken over.

    Folded after casefolding as well as before, because casefolding a composed
    character can leave a decomposed one.

    Whole names are compared, so two entries under directories that differ only
    this way — ``artifacts/a/x.bin`` beside ``artifacts/A/y.bin`` — are allowed
    and land in one directory. Nothing is lost and nothing is mixed up: both
    files arrive, under the names the manifest reads them back by. It is only
    the tree's spelling that stops matching the package's.
    """
    return unicodedata.normalize("NFC", unicodedata.normalize("NFC", name).casefold())


def _shown(name: str) -> str:
    """A name written so that two of them can be compared by eye.

    The refusal below names both entries, and the pair it exists to catch can be
    two spellings of the same word — composed and decomposed, which print
    identically. Escaping those is the difference between a message that says
    what is wrong and one that appears to name the same file twice.

    `ascii` throughout rather than only for the names that need it: it is what
    `repr` already does for anything inside ASCII, so choosing between them
    would be a branch that cannot be observed.
    """
    return ascii(name)


def _enclosing_directories(folded: str) -> list[str]:
    """Every directory the name is written inside, outermost first.

    Folded names, so the answer is what the filesystem will have to make rather
    than what the archive spelled. Segments rather than string prefixes:
    ``artifacts/b.bin`` starts with the same characters as ``artifacts/b`` and
    is not inside it.
    """
    segments = folded.split("/")
    return ["/".join(segments[:depth]) for depth in range(1, len(segments))]


def _cannot_both_be_written(file_name: str, inner_name: str) -> str:
    """Why a pair of names has no tree that holds both.

    Both are named because either one can be the one to change, and the message
    is all its holder gets — the endpoint hands this text straight on.
    """
    return (
        f"{_shown(file_name)} is a file and {_shown(inner_name)} is inside it, so they "
        "cannot both be written and this package was not opened"
    )


def _decode_steps(entries: Mapping[str, bytes]) -> dict[str, str]:
    """Read every step as text, or refuse the package.

    Done while opening rather than when the gate asks: a step that cannot be
    decoded is one that cannot be checked, and that is not the same as one that
    passed. Selecting on a lowercase `.py` suffix would miss `02.PY` on a
    case-insensitive filesystem, where a runner globbing `steps/*.py` still
    finds it — so everything under the prefix is taken.
    """
    sources = {}
    for name, blob in entries.items():
        if not name.startswith(STEPS_PREFIX):
            continue
        try:
            sources[name] = blob.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ClsError(f"{name!r} is not valid utf-8, so it cannot be checked") from exc
    return sources


def _budget_for(fp: BinaryIO) -> int:
    """How many bytes this archive is allowed to expand into."""
    return min(MAX_UNCOMPRESSED_BYTES, _size_of(fp) * MAX_EXPANSION_RATIO)


def _read_entries(archive: zipfile.ZipFile, *, budget: int) -> dict[str, bytes]:
    infos = archive.infolist()
    if len(infos) > MAX_ENTRIES:
        raise ClsError(f"this package has {len(infos)} entries; the limit is {MAX_ENTRIES}")

    # Names first, bytes second. Every name in the package is checked against
    # every other before a single member is decompressed, so a package refused
    # for its names costs only its names — and the entries that would have been
    # read before the offending one are no longer read at all.
    members: dict[str, zipfile.ZipInfo] = {}
    by_folded_name: dict[str, str] = {}
    for info in infos:
        # Before the is-a-directory skip below, which reads the same overridable
        # name: an override ending in a separator would otherwise pose as a
        # directory and miss every check under it while its content still ships.
        _reject_ambiguous_name(info)
        try:
            # The same name rules the packer applies. Re-raised as a read error
            # because which side is at fault differs: there it is our item that
            # cannot travel, here it is their package that cannot be opened.
            safe_entry_name(info.filename.rstrip("/"))
        except PackError as exc:
            raise ClsError(str(exc)) from exc
        _reject_unknown_layout(info.filename)
        if info.is_dir():
            continue
        if stat.S_ISLNK(info.external_attr >> 16):
            raise ClsError(f"{info.filename!r} is a symlink entry, which is not stored")
        _reject_unreadable_member(info)
        if info.filename in members:
            raise ClsError(
                f"{info.filename!r} is a duplicate entry, so this package was not opened"
            )
        # A package that cannot be written out as it was checked is refused
        # whatever it holds.
        folded = _folded(info.filename)
        if folded in by_folded_name:
            raise ClsError(
                f"{_shown(info.filename)} and {_shown(by_folded_name[folded])} cannot be "
                "told apart by every filesystem this package may be written to, so it was "
                "not opened"
            )
        by_folded_name[folded] = info.filename
        members[info.filename] = info

    _reject_a_directory_a_file_already_holds(by_folded_name)

    entries: dict[str, bytes] = {}
    remaining = budget
    for name, info in members.items():
        blob = _read_member(archive, info, remaining=remaining)
        remaining -= len(blob)
        entries[name] = blob
    return entries


def _reject_a_directory_a_file_already_holds(by_folded_name: Mapping[str, str]) -> None:
    """Refuse a package that no tree can hold.

    Whole names are compared as they arrive, so a name that is not another
    entry's name but *is* the directory that entry sits in gets that far
    untouched — and one of the two is then impossible to write: a `mkdir` onto
    a file, or a write onto a directory. Left to the write it surfaces halfway
    through unpacking, as this machine failing rather than as the package being
    wrong, which is the one thing its holder could act on.

    Asked once over the finished set rather than as each name arrives, because
    the question is symmetric and the answer must not depend on which of the
    two the directory happens to list first. Only the names are held; the
    directories they imply are walked and dropped, so the cost of asking does
    not grow with how deeply a package nests.
    """
    for folded, name in by_folded_name.items():
        for directory in _enclosing_directories(folded):
            holder = by_folded_name.get(directory)
            if holder is not None:
                raise ClsError(_cannot_both_be_written(holder, name))


def _read_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo, *, remaining: int) -> bytes:
    """Read one entry within the bytes still available.

    Read in chunks against a running budget rather than asking for the declared
    size: that size is written by whoever built the archive, and a single read
    of it would materialise the whole expansion before anyone checked it.

    Three ways a member can fail to be read, none of them a `BadZipFile` and all
    of them the package being wrong. What they have in common is that the
    directory does not say so: unlike a compression method or an encryption bit,
    these are only found by going to where the entry claims its bytes are — which
    is why these refusals are here rather than beside the others.

    - Bytes that are not the deflate stream they are stored as raise out of
      `zlib` while decompressing.
    - An entry whose data would begin past the end of the file reads nothing
      where the bytes should be, and `zipfile` raises a bare `EOFError`.
    - An entry whose recorded position lands before the start of the file — an
      archive can move every position at once by misdeclaring where its
      directory begins — makes `zipfile` seek to a negative offset, which the
      buffer refuses with `ValueError`.

    Enumerated rather than caught wholesale. `ClsError` derives from
    `RuntimeError` and none of these three do, so the budget refusal raised
    inside this block still passes through as itself; a net wide enough to take
    that would report a zip bomb as bytes that would not decompress.
    """
    chunks: list[bytes] = []
    left = remaining
    try:
        with archive.open(info) as handle:
            while chunk := handle.read(min(READ_CHUNK_BYTES, left + 1)):
                left -= len(chunk)
                if left < 0:
                    raise ClsError("this package expands far beyond its size and was not opened")
                chunks.append(chunk)
    except zlib.error as exc:
        raise ClsError(
            f"{info.filename!r} does not decompress, so this package was not opened: {exc}"
        ) from exc
    except (EOFError, ValueError) as exc:
        # Said without quoting the exception: an `EOFError` from here carries no
        # message at all, and a negative seek reports an offset that means
        # nothing to whoever holds the package.
        raise ClsError(
            f"{info.filename!r} does not lie where the package says it does, "
            "so this package was not opened"
        ) from exc
    return b"".join(chunks)


def _reject_unreadable_member(info: zipfile.ZipInfo) -> None:
    """Refuse a member that opening would raise on rather than return.

    Asked from the directory, beside the other checks, rather than left to the
    read: `zipfile` decides both of these from the same two fields, so the answer
    here is the answer there — and a package refused on them costs only its
    names, like every other refusal in this loop.

    Left to the read, either one surfaces as `NotImplementedError` or
    `RuntimeError`, neither of which is a `BadZipFile`. They pass the reader's
    own refusals untouched and reach the endpoint as this machine having failed,
    when what is wrong is a package its holder could repack.
    """
    if info.compress_type not in SUPPORTED_COMPRESSION:
        # Named from `zipfile`'s own table where it knows the number, because the
        # message is what its holder repacks from and "implode" is actionable
        # where "method 6" is a search away. The number is kept for the methods
        # the table has never heard of.
        #
        # Reached for defensively: the table is not in that module's `__all__`,
        # and this is the one line in the reader where an `AttributeError` would
        # land as the machine failing on the very path that exists to stop
        # exactly that.
        names = getattr(zipfile, "compressor_names", {})
        method = names.get(info.compress_type, f"method {info.compress_type}")
        raise ClsError(
            f"{info.filename!r} is compressed with {method}, which is not one this format "
            "is written with, so this package was not opened"
        )
    if info.flag_bits & UNREADABLE_MEMBER_FLAGS:
        stored_as = (
            "stored as a patch rather than as the file itself"
            if info.flag_bits & COMPRESSED_PATCH_FLAG
            else "encrypted"
        )
        raise ClsError(f"{info.filename!r} is {stored_as}, so this package was not opened")


def _reject_ambiguous_name(info: zipfile.ZipInfo) -> None:
    """The name acted on has to be the name every other reader sees.

    CPython lets an archive override an entry's name through the optional
    0x7075 extra field, and separately rewrites it while sanitising. Either way
    it stops matching the central directory — so an entry can be checked as one
    file and unpack as another, and the digest names a different payload per
    reader.
    """
    if info.filename != info.orig_filename:
        raise ClsError(
            f"{info.orig_filename!r} reads as {info.filename!r}; "
            "a package whose names disagree with its directory is not opened"
        )
    if not info.orig_filename.isascii() and not info.flag_bits & UTF8_NAME_FLAG:
        raise ClsError(f"{info.orig_filename!r} does not declare its name as UTF-8")


def _reject_unknown_layout(name: str) -> None:
    if name in ALLOWED_TOP_LEVEL or name.startswith(ALLOWED_PREFIXES):
        return
    raise ClsError(f"{name!r} is outside the .cls layout, so this package was not opened")


def _read_metadata(entries: Mapping[str, bytes]) -> dict[str, Any]:
    raw = entries.get(META_NAME)
    if raw is None:
        raise ClsError(f"this package has no {META_NAME}")
    try:
        document = json.loads(raw)
    except (ValueError, RecursionError) as exc:
        # ValueError covers malformed JSON and CPython's cap on integer-literal
        # digits; deeply nested containers raise RecursionError instead. All of
        # them are an untrusted document being wrong.
        raise ClsError(f"{META_NAME} is not valid JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise ClsError(f"{META_NAME} must be a JSON object")

    version = document.get("format_version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise ClsError(f"{META_NAME} has no usable format_version")
    if version not in SUPPORTED_FORMAT_VERSIONS:
        raise ClsError(
            f"this package declares format_version {version}, which this version cannot read"
        )
    for field in ("content_version", "license"):
        value = document.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ClsError(f"{META_NAME} has no {field}")

    # Unknown keys are kept: the format may grow fields this version has no
    # opinion about, and they are inside the digest — dropping them would lose
    # data the publisher shipped deliberately.
    return document


def verify_steps(package: ClsPackage) -> list[StepRefusal]:
    """Run the code gate over every step, before any of it is executed.

    The same check an upload gate runs, for the reason that gate running it is
    not enough: it saw the package once, and this one
    may have been edited since or never have been through it at all. The threat
    lands here — this is the machine that runs the code.
    """
    refused = []
    for path, source in sorted(package.steps().items()):
        verdict = validate_code(source)
        if not verdict.ok:
            refused.append(StepRefusal(path=path, reasons=tuple(verdict.reasons)))
    return refused
