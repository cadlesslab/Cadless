"""The synthetic arrival the catalogue tests register for themselves.

`depot` is a name no shipping build uses, so a reader cannot mistake these tests
for tests of a real one. The registry, and the routes that read it, answer for
*whatever* origins a build has registered — exercising them against one the test
registers itself is what keeps that mechanism the subject, where naming a
particular build's origin would quietly test that build.

Shared because two files needed the same thing and their copies had already
drifted: one read `digest` and the other parsed the package sentence, which is
two answers to one question. The reader below is the union of both, so each file
keeps the behaviour it was asserting.
"""

from __future__ import annotations

import pytest

from cadless.catalog.origins import (
    ItemOrigin,
    Origin,
    recorded_text,
    register_origin,
    unregister_origin,
)

DEPOT_KEY = "depot"
DEPOT_LABEL = "Depot"
DEPOT_SENTENCE = f"imported from the {DEPOT_KEY}"
DEPOT_PACKAGE = f"{DEPOT_SENTENCE} package "
# Shapes the ids take: 32 hex is what a catalogue issues, 64 is a digest.
DEPOT_CATALOG_ID = "3f2a" * 8
DEPOT_DIGEST = "5e0a" * 16


def depot_reader(provenance, licence):
    """A registered origin recognising its own records, in both shapes.

    The structured one is what a build writes today. The sentence is what an
    older fetch left behind, and reading it back is the whole reason a reader is
    a function rather than a key lookup.
    """
    recorded = provenance.get(DEPOT_KEY)
    if isinstance(recorded, dict):
        return ItemOrigin(
            DEPOT_KEY,
            catalog_id=recorded_text(recorded.get("catalog_id")),
            version_id=recorded_text(recorded.get("version_id")),
            digest=recorded_text(recorded.get("digest")),
            licence=licence,
        )
    dataset = provenance.get("dataset")
    if isinstance(dataset, str) and (
        dataset == DEPOT_SENTENCE or dataset.startswith(DEPOT_PACKAGE)
    ):
        spoken = dataset[len(DEPOT_PACKAGE) :].split() if dataset != DEPOT_SENTENCE else []
        return ItemOrigin(
            DEPOT_KEY,
            catalog_id=spoken[0] if spoken else None,
            licence=licence,
        )
    return None


@pytest.fixture
def depot_origin():
    """A build that ships its own arrival, for the duration of one test."""
    origin = register_origin(
        Origin(key=DEPOT_KEY, label=DEPOT_LABEL, sort_order=10, reader=depot_reader)
    )
    yield origin
    unregister_origin(DEPOT_KEY)
