"""The `.cls` container as it is read, and the digest that identifies one.

The digest tests are the important ones. A `.cls` package is identified by a
canonical digest that a writer and a reader compute independently, and handing
one over is only meaningful if the two agree — so these tests derive the
expected value from the specification itself rather than from this module, and
pin the published test vector on top of that.

Assembling a package is a writer's job, and no writer ships in this build. What
stays here is what core import depends on and what both halves have to agree
about: the digest, and the rule for an entry name — which is asked of both the
name a packer writes and the name that arrives inside somebody else's archive.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from cadless.catalog.pack import PackError, canonical_digest, safe_entry_name


def sha256_hex(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


# The published vector: these three entries hash to the value below. It is the
# only check here that could catch the whole formula being wrong in a way that
# is self-consistent, because the value comes from the other implementation.
PUBLISHED_VECTOR_META = {
    "format_version": 1,
    "content_version": "1.0.0",
    "license": "CC-BY-4.0",
    "title": "L bracket",
    "min_tool_version": "0.1.0",
    "tags": ["bracket"],
    "included_fields": ["artifacts", "steps"],
}


PUBLISHED_VECTOR_ENTRIES = {
    "cls.json": json.dumps(PUBLISHED_VECTOR_META, sort_keys=True).encode("utf-8"),
    "steps/01.py": b"result = 1 + 1\n",
    "artifacts/01/model.stl": b"solid\n",
}


PUBLISHED_VECTOR_DIGEST = "be6410178a106e4cc444940ba07701fc80ac24ed52575b671a12fd54b0df8985"


def test_digest_matches_the_published_test_vector():
    """Cross-implementation check against a value we did not compute."""
    assert canonical_digest(PUBLISHED_VECTOR_ENTRIES) == PUBLISHED_VECTOR_DIGEST


def test_digest_is_the_sha256_of_the_path_sorted_entry_manifest():
    """Spelled out from the specification, not by calling the code under test.

    Each line is `{sha256}:{path byte length}:{path}\\n`. The length prefix is
    what makes the encoding injective: without it an entry name containing a
    newline could spell out an extra line, and two different packages could
    hash alike.
    """
    entries = {"steps/01.py": b"result = 1\n", "cls.json": b"{}"}
    manifest = (
        f"{sha256_hex(b'{}')}:8:cls.json\n{sha256_hex(b'result = 1\n')}:11:steps/01.py\n"
    ).encode()

    assert canonical_digest(entries) == sha256_hex(manifest)


def test_digest_orders_lines_by_path_not_by_content_hash():
    entries = {"b.txt": b"1", "a.txt": b"2"}
    by_path = f"{sha256_hex(b'2')}:5:a.txt\n{sha256_hex(b'1')}:5:b.txt\n".encode()

    # Guards the guard: if the two content hashes happened to sort the same way
    # as the paths, a hash-ordered implementation would pass this by accident.
    assert sha256_hex(b"2") > sha256_hex(b"1")
    assert canonical_digest(entries) == sha256_hex(by_path)


def test_digest_measures_the_path_in_bytes_not_characters():
    """The length prefix is a byte count, and a non-ASCII name is where a
    character count would silently diverge from a receiver's value."""
    name = "artifacts/모델.stl"
    assert len(name) != len(name.encode("utf-8"))

    expected = b"%s:%d:%s\n" % (
        sha256_hex(b"solid\n").encode(),
        len(name.encode("utf-8")),
        name.encode("utf-8"),
    )

    assert canonical_digest({name: b"solid\n"}) == sha256_hex(expected)


def test_digest_refuses_an_empty_entry_name():
    """Skipping it quietly would let two different packages hash alike, which
    is the one thing this function exists to prevent."""
    with pytest.raises(ValueError, match="empty"):
        canonical_digest({"": b"payload"})


def test_digest_excludes_the_checksums_and_signature_blocks():
    """A signature is taken over this digest; hashing it in would change the
    very value being signed."""
    base = {"cls.json": b"{}", "steps/01.py": b"result = 1\n"}

    assert canonical_digest({**base, "checksums": b"whatever"}) == canonical_digest(base)
    assert canonical_digest({**base, "signature": b"sig"}) == canonical_digest(base)


def test_digest_covers_cls_json():
    """Metadata rides in `cls.json`, so leaving it out of the digest would let
    the licence and the version be rewritten without detection."""
    base = {"steps/01.py": b"result = 1\n"}

    assert canonical_digest({**base, "cls.json": b"{}"}) != canonical_digest(base)


@pytest.mark.parametrize(
    ("filler", "count"),
    [("\U0001f600", 64), ("가", 86), ("é", 128)],
    ids=["emoji", "hangul", "e-acute"],
)
def test_a_name_segment_no_filesystem_could_hold_is_refused_here_too(filler, count):
    """The packer and the reader ask one function, so both refuse the same names.

    Asked of that function directly rather than through `build_cls`: a name this
    rule refuses is one the filesystem refuses first, so on Linux — where this
    suite's CI runs — the file could not be created to pack. Which is also why
    the rule is worth having. Our packer cannot produce such a name; a package
    written elsewhere can still carry one, and that is what the reader meets.
    """
    with pytest.raises(PackError, match="bytes"):
        safe_entry_name(f"artifacts/{filler * count}")
