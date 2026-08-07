"""Every store method is classified, and the classification is checked here.

The acceptance criterion for the identity seam was "every read path, not a
sample". A hand-written list satisfies that on the day it is written and decays
from the first method added afterwards — and the decay is silent, because an
unscoped method looks exactly like a scoped one from the outside.

So the list is derived rather than written. Each public method of
:class:`~cadless.store.Store` has to fall into one of three sets below, and a
method that falls into none of them fails this file until somebody decides which
it is. That decision is the point: it costs one line and a sentence, and it is
the moment where "does this read somebody's rows?" gets asked.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from backend.schemas import ProjectOut
from cadless.scoped_store import ScopedStore
from cadless.store import Store

_ROOT = Path(__file__).resolve().parent.parent

# Not reachable from a request at all, and not on the scoped view. Each of these
# legitimately acts for the whole installation.
UNSCOPED_BY_DESIGN = {
    # Creates the schema and migrates it. Runs once at startup, before any
    # request exists.
    "init",
    # Compares the referenced set against what is on disk. Scoped, it would
    # report every other owner's blobs as unreferenced and delete them.
    "all_artifact_paths",
    # The sweep itself, for the same reason.
    "sweep_orphans",
    # The legacy ledger back-fill's helper. It files the row under the build,
    # because a catalogue item is the build's — so a route able to call it could
    # hand its own project to every principal by declaring it one.
    "set_catalog_item_id",
}

# On the scoped view but taking no owner, because they read no rows.
EXPOSED_WITHOUT_OWNER = {
    # Derives a directory path from an integer and makes sure it exists. Serving
    # the bytes goes through get_artifact, which is scoped.
    "version_artifact_dir",
}


def _public_methods(cls: type) -> set[str]:
    return {
        name
        for name, _ in inspect.getmembers(cls, predicate=inspect.isfunction)
        if not name.startswith("_")
    }


STORE_METHODS = _public_methods(Store)
SCOPED_METHODS = _public_methods(ScopedStore)
MUST_BE_SCOPED = STORE_METHODS - UNSCOPED_BY_DESIGN - EXPOSED_WITHOUT_OWNER


def test_the_store_actually_has_the_methods_this_file_exempts():
    """A stale exemption is worse than none — it silently covers nothing."""
    assert UNSCOPED_BY_DESIGN <= STORE_METHODS
    assert EXPOSED_WITHOUT_OWNER <= STORE_METHODS


def test_every_store_method_is_classified():
    # This is the assertion that survives the next person adding a method: a new
    # public method belongs to no set, so it lands in MUST_BE_SCOPED and fails
    # the two tests below until it is either scoped or exempted on purpose.
    assert STORE_METHODS == MUST_BE_SCOPED | UNSCOPED_BY_DESIGN | EXPOSED_WITHOUT_OWNER


@pytest.mark.parametrize("name", sorted(MUST_BE_SCOPED))
def test_a_scoped_method_accepts_an_owner(name):
    params = inspect.signature(getattr(Store, name)).parameters
    assert "owner" in params, (
        f"Store.{name} reads or writes rows but takes no owner. Either scope it, "
        f"or add it to UNSCOPED_BY_DESIGN / EXPOSED_WITHOUT_OWNER with the reason."
    )
    assert params["owner"].kind is inspect.Parameter.KEYWORD_ONLY, (
        f"Store.{name}'s owner must be keyword-only, so it cannot be supplied "
        f"by position and cannot be shifted by a later parameter."
    )


@pytest.mark.parametrize("name", sorted(MUST_BE_SCOPED))
def test_a_scoped_method_is_reachable_from_a_request(name):
    assert name in SCOPED_METHODS, (
        f"Store.{name} is scoped but absent from ScopedStore, so no route can "
        f"call it. Add it there, or exempt it here with the reason."
    )


@pytest.mark.parametrize("name", sorted(UNSCOPED_BY_DESIGN))
def test_an_unscoped_method_is_not_reachable_from_a_request(name):
    assert name not in SCOPED_METHODS, (
        f"ScopedStore exposes {name}, which is exempt from scoping. A route can "
        f"now reach across owners through it."
    )


def test_the_scoped_view_invents_nothing():
    """Everything on the scoped view is a store method, so there is one place to audit."""
    assert SCOPED_METHODS <= STORE_METHODS


@pytest.mark.parametrize("name", sorted(MUST_BE_SCOPED))
def test_the_scoped_view_does_not_ask_a_route_for_an_owner(name):
    # The caller must not be able to choose. If `owner` reached the scoped
    # signature, a route could pass somebody else's.
    params = inspect.signature(getattr(ScopedStore, name)).parameters
    assert "owner" not in params, (
        f"ScopedStore.{name} takes an owner, so a route can choose one. The "
        f"principal is the only answer, and it comes from the dependency."
    )


# Two methods deliberately narrow what they forward rather than mirroring the
# store, and each is a capability a request must not have.
NARROWED_ON_PURPOSE = {
    # `catalog_item_id` would mint a row that refuses every mutation, cannot be
    # deleted through the project route, and cannot be reached through the
    # catalogue route either. Only the build's view creates catalogue items.
    "create_project": {"catalog_item_id"},
}


@pytest.mark.parametrize("name", sorted(MUST_BE_SCOPED))
def test_the_scoped_view_forwards_faithfully(name):
    """The failure hand-written delegation actually risks.

    Every method here forwards to the store by hand, several of them with a
    long run of positional arguments. A pair swapped in `add_version` (eleven of
    them) or `add_message` (seven) would compile, pass every behavioural test
    that does not happen to distinguish the two, and be wrong. Comparing the
    signatures is what holds them in step.
    """
    store_params = dict(inspect.signature(getattr(Store, name)).parameters)
    scoped_params = dict(inspect.signature(getattr(ScopedStore, name)).parameters)
    store_params.pop("owner", None)
    for dropped in NARROWED_ON_PURPOSE.get(name, ()):
        store_params.pop(dropped, None)

    assert list(scoped_params) == list(store_params), (
        f"ScopedStore.{name} does not take the same arguments in the same order "
        f"as Store.{name}; a positional forward is misaligned."
    )
    for param, expected in store_params.items():
        actual = scoped_params[param]
        assert actual.kind is expected.kind, f"ScopedStore.{name}: {param} changed kind"
        assert actual.default == expected.default, f"ScopedStore.{name}: {param} changed default"


def test_the_owner_column_is_not_published_in_a_project_response():
    # ProjectOut lists its fields by hand rather than reading them off the
    # dataclass, which is what keeps the column off the wire. Swapping it for
    # `from_attributes` would publish it the day it was added.
    assert "owner" not in ProjectOut.model_fields


# ---- the boundary a route may not cross -------------------------------------


def _imports(path: Path) -> list[ast.ImportFrom]:
    tree = ast.parse(path.read_text())
    return [n for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]


ROUTER_FILES = sorted((_ROOT / "backend" / "routers").glob("*.py"))


def test_there_are_routers_to_check():
    """Guards the glob: an empty list would make the next test vacuously true."""
    assert len(ROUTER_FILES) >= 10


@pytest.mark.parametrize("path", ROUTER_FILES, ids=lambda p: p.name)
def test_a_router_does_not_import_the_unscoped_store(path):
    """Rows reach a route through the scoped view or not at all.

    The dataclasses are fine — they are shapes, not access. What a router must
    not have is the class itself or the process singleton, either of which would
    let it read across owners while looking exactly like ordinary code.
    """
    forbidden = {
        # The unscoped store itself, and the process singleton that hands it out.
        "cadless.store": {"Store", "get_store"},
        # Widening a view back to the build. Legitimate inside the catalogue
        # loader, where an item genuinely belongs to the installation; from a
        # route it is a way to read and write rows every principal shares.
        "cadless.scoped_store": {"system_view", "SYSTEM"},
    }
    for node in _imports(path):
        taken = {alias.name for alias in node.names} & forbidden.get(node.module, set())
        assert not taken, (
            f"{path.name} imports {sorted(taken)} from {node.module}. A route "
            f"reaches rows through backend.deps.get_store, which is scoped to "
            f"the caller and cannot be widened."
        )
