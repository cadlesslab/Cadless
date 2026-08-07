"""Edit the records a zip keeps about its entries, byte by byte.

A package that is wrong in one of these ways cannot be produced with `writestr`:
it refuses a compression method it cannot write, sets the general purpose bits
itself, and computes every offset. So the suites that check what the reader does
with such a package build a sound one and then edit its bytes.

Kept here rather than in each of them because the offsets are the sort of
constant that is wrong in exactly one place and right in the other — and because
what an entry declares about itself is written twice, in the local header and
again in the central directory, which is a detail every caller would otherwise
have to remember.
"""

from __future__ import annotations

LOCAL_HEADER = b"PK\x03\x04"
CENTRAL_HEADER = b"PK\x01\x02"
END_RECORD = b"PK\x05\x06"

# An entry is written twice. A local header begins 30 bytes before the name it
# carries and a central directory record 46, so each record is found by finding
# its name and stepping back. The offsets below are counted from a record's
# start, and the two in each pair are (local, central).
LOCAL_HEADER_BYTES = 30
CENTRAL_RECORD_BYTES = 46
NAME_LENGTH_OFFSETS = (26, 28)
FLAG_OFFSETS = (6, 8)
METHOD_OFFSETS = (8, 10)

# Fields only one of the two records has.
LOCAL_COMPRESSED_SIZE = 18
LOCAL_EXTRA_LENGTH = 28
CENTRAL_EXTRACT_VERSION = 6
CENTRAL_HEADER_OFFSET = 42
# In the end of central directory record: where the directory is said to begin.
END_DIRECTORY_OFFSET = 16

# Which of a member's stored bytes to scramble. The first two are left alone so
# the stream still begins as a well-formed deflate block and fails partway
# through, which is where a reader guarding only the opening would let it past.
SCRAMBLE_START = 2
SCRAMBLE_END = 12


def records(payload: bytes, name: str) -> tuple[int, int]:
    """Where `name`'s two records begin: the local header, then the directory.

    The recorded name length is compared as well as the signature, because one
    entry's name can be another's prefix — `artifacts/01/` is found inside
    `artifacts/01/model.stl`, and stepping back from there lands on a record
    belonging to the wrong entry.
    """
    raw = name.encode()
    found: list[list[int]] = [[], []]
    at = payload.find(raw)
    while at != -1:
        for which, (signature, back) in enumerate(
            ((LOCAL_HEADER, LOCAL_HEADER_BYTES), (CENTRAL_HEADER, CENTRAL_RECORD_BYTES))
        ):
            record = at - back
            length = record + NAME_LENGTH_OFFSETS[which]
            if payload[record : record + 4] == signature and int.from_bytes(
                payload[length : length + 2], "little"
            ) == len(raw):
                found[which].append(record)
        at = payload.find(raw, at + 1)
    # Every match is kept and exactly one demanded, rather than the last one
    # winning. A record is recognised by a signature and a length, and both can
    # occur inside another member's stored bytes — so a second match means the
    # answer is a guess, and a helper that guesses edits the wrong entry and
    # leaves the test passing for the wrong reason.
    assert [len(each) for each in found] == [1, 1], (
        f"expected one local and one central record for {name!r}, found {found}"
    )
    return found[0][0], found[1][0]


def rewritten(
    payload: bytes, name: str, *, method: int | None = None, flag: int | None = None
) -> bytes:
    """The same archive, with one entry's method or flag bits changed by hand.

    `method` replaces the compression method; `flag` is set in addition to the
    bits already there, since the ones an archive sets legitimately have to stay.

    Both records are edited so the archive stays internally consistent. Only the
    central directory would decide the outcome — `zipfile` reads compression and
    flags from there and consults the local header only for the name — but an
    archive whose two copies disagreed would be testing that disagreement rather
    than the field under test.
    """
    assert (method is None) != (flag is None), "give exactly one of method or flag"
    out = bytearray(payload)
    offsets = METHOD_OFFSETS if method is not None else FLAG_OFFSETS
    for record, offset in zip(records(payload, name), offsets, strict=True):
        start = record + offset
        current = int.from_bytes(out[start : start + 2], "little")
        value = method if method is not None else current | flag
        out[start : start + 2] = value.to_bytes(2, "little")
    return bytes(out)


def with_a_scrambled_stream(payload: bytes, name: str) -> bytes:
    """The same archive, with one entry's compressed bytes no longer deflate.

    Edited in place so every offset the archive records still lands: what changes
    is only whether the bytes decompress. The first two bytes are left alone so
    the stream begins as a well-formed deflate block and fails partway through,
    which is where a reader that only guarded the opening would let it past.
    """
    record = records(payload, name)[0]
    out = bytearray(payload)
    extra = int.from_bytes(out[record + LOCAL_EXTRA_LENGTH :][:2], "little")
    stored = int.from_bytes(out[record + LOCAL_COMPRESSED_SIZE :][:4], "little")
    # Checked rather than assumed: scrambling past the end of this member's
    # bytes would corrupt the next entry's header instead, and the archive would
    # then be refused for a reason that has nothing to do with the test.
    assert stored >= SCRAMBLE_END, f"{name!r} holds {stored} bytes, too few to scramble"
    start = record + LOCAL_HEADER_BYTES + len(name.encode()) + extra
    for index in range(start + SCRAMBLE_START, start + SCRAMBLE_END):
        out[index] ^= 0xFF
    return bytes(out)
