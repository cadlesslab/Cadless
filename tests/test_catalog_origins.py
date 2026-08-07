"""Origin registry tests: where an item came from is data, not code.

The registry itself, and `origin_of` reading a `source.json` through it. None of
this names a particular arrival: the one that is not built in is played by a
synthetic origin registered for the duration of a test, which is exactly what a
build shipping its own arrival does.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from cadless.catalog.origins import (
    Origin,
    all_origins,
    find_origin,
    get_origin,
    origin_label,
    origin_of,
    origin_sort_key,
    recorded_text,
    register_origin,
)


# The synthetic arrival, and the reader that recognises it, both live in
# `tests/depot_origin.py` — this file and `test_catalog_api.py` had a copy each
# and the two had already drifted.
@pytest.fixture
def depot(depot_origin):
    """This file's name for the shared fixture; its cases read better as `depot`."""
    return depot_origin


# --------------------------------------------------------------------------- #
# the registry
# --------------------------------------------------------------------------- #


def test_builtin_origins_registered():
    assert get_origin("local").label == "Local"
    assert get_origin("file").label == "File"


def test_the_engine_ships_no_arrival_it_does_not_implement():
    """Importing the catalog engine registers `local` and `file`, and nothing else.

    In a subprocess, and that is the point rather than fussiness. The registry
    is process-global, and a build's router registers its own origin when it is
    imported — so in the one process the whole suite shares, whatever any other
    test imported is still registered here. Asked in a fresh interpreter that
    imports only this module, the question has a stable answer, and it is the
    question the seam actually turns on: the engine on its own knows no arrival
    it does not implement.
    """
    out = subprocess.run(
        [
            sys.executable,
            "-c",
            "from cadless.catalog.origins import all_origins;"
            "print(sorted(o.key for o in all_origins()))",
        ],
        capture_output=True,
        text=True,
        check=True,
        cwd=Path(__file__).resolve().parent.parent,
    )
    assert out.stdout.strip() == "['file', 'local']"


def test_get_origin_unknown_raises_with_known_keys():
    with pytest.raises(ValueError, match="registered"):
        get_origin("bogus")
    assert find_origin("bogus") is None


def test_register_duplicate_key_raises():
    with pytest.raises(ValueError, match="file"):
        register_origin(Origin(key="file", label="Other"))


@pytest.mark.parametrize("key", ["a/b", "A", "", "-x", "a b", "..", "a%2Fb"])
def test_a_key_that_is_not_one_path_segment_is_refused(key):
    """It becomes a path segment, so one that cannot be addressed is a trap.

    `/catalog/origins/{kind}` is how a panel asks what it already holds. A key
    with a slash does not error there, it simply never matches — the build would
    get an origin that labels correctly and answers nothing, with nothing said
    anywhere. Refused at registration instead.
    """
    with pytest.raises(ValueError, match="path segment"):
        register_origin(Origin(key=key, label="Nope"))


def test_register_replace_overrides():
    register_origin(Origin(key="file", label="Handed over", sort_order=20), replace=True)
    try:
        assert get_origin("file").label == "Handed over"
    finally:
        register_origin(Origin(key="file", label="File", sort_order=20), replace=True)


def test_a_registered_origin_sorts_between_the_builtins(depot):
    """Relative, not absolute: another build's router may have registered too."""
    order = [o.key for o in all_origins()]
    assert order.index("local") < order.index("depot") < order.index("file")


def test_origin_label_falls_back_for_an_unregistered_key():
    """An item recorded by a build this one does not have still says something."""
    assert origin_label("depot") == "Depot"


def test_origin_sort_key_puts_an_unregistered_key_last():
    assert origin_sort_key("depot") > origin_sort_key("file")


# --------------------------------------------------------------------------- #
# origin_of
# --------------------------------------------------------------------------- #


def test_provenance_that_is_not_a_record_is_not_called_anything():
    assert origin_of(None).kind == "unknown"
    assert origin_of("not a mapping").kind == "unknown"


def test_a_record_that_never_said_is_not_called_anything():
    assert origin_of({"license": "MIT"}).kind == "unknown"


def test_the_terms_survive_not_knowing_where_it_came_from():
    """Both `unknown` paths keep the licence, and they have to agree.

    The one question the licence is for — may this be passed on — is asked in
    the same breath as where the copy came from. Answering the second with
    "cannot tell" while dropping the first leaves a publisher acknowledging an
    original whose terms nobody showed them.
    """
    # No `dataset` at all.
    assert origin_of({"license": "MIT"}).licence == "MIT"
    # A key from a build that is not installed here.
    said = origin_of({"dataset": "imported from the depot", "depot": {}, "license": "MIT"})
    assert (said.kind, said.licence) == ("unknown", "MIT")


def test_a_package_handed_over_directly_reads_as_a_file():
    got = origin_of({"dataset": "imported from a-colleague.cls", "license": "MIT"})
    assert got.kind == "file"
    assert got.licence == "MIT"


def test_a_registered_origin_claims_its_own_reference(depot):
    got = origin_of(
        {
            "dataset": "imported from the depot package one two",
            "depot": {"catalog_id": "one", "version_id": "two"},
            "license": "MIT",
        }
    )
    assert got.kind == "depot"
    assert (got.catalog_id, got.version_id, got.licence) == ("one", "two", "MIT")


def test_a_registered_origin_reads_back_its_own_older_sentence(depot):
    """The reference came later; the sentence is all an early arrival wrote."""
    got = origin_of({"dataset": "imported from the depot package one", "license": "MIT"})
    assert (got.kind, got.catalog_id) == ("depot", "one")


def test_a_sentence_that_merely_begins_like_one_is_not_that_origin(depot):
    got = origin_of({"dataset": "imported from the depot-next-door", "license": "MIT"})
    assert got.kind == "file"


def test_an_item_recorded_by_a_build_this_one_does_not_have_is_not_called_a_file():
    """The point of the whole seam, stated as a test.

    With no `depot` registered, this record is unreadable here. Calling it
    `file` would claim it was handed over directly — on the strength of not
    recognising it — and the same item would change its story depending on
    which build opened it.
    """
    record = {
        "dataset": "imported from the depot package one two",
        "depot": {"catalog_id": "one"},
        "license": "MIT",
    }
    got = origin_of(record)
    assert got.kind == "unknown"
    assert got.licence == "MIT"


def test_the_same_record_reads_as_its_own_origin_once_that_build_is_present(depot):
    """The other half of the test above: registering the origin is the whole fix."""
    record = {
        "dataset": "imported from the depot package one two",
        "depot": {"catalog_id": "one"},
        "license": "MIT",
    }
    assert origin_of(record).kind == "depot"


def test_a_reference_holding_the_wrong_shape_does_not_take_the_listing_down(depot):
    """These files are hand-editable; one bad value must not answer with a 500."""
    got = origin_of({"dataset": "imported from the depot", "depot": {"catalog_id": 123}})
    assert got.kind == "depot"
    assert got.catalog_id is None


def test_recorded_text_keeps_only_strings():
    assert recorded_text("x") == "x"
    assert recorded_text(123) is None
    assert recorded_text(None) is None
