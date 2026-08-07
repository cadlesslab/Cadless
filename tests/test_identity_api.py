"""The seam as a build would actually use it, over HTTP.

`test_store_scoping` proves the store keeps two owners apart. This proves the
same thing through the API with a resolver registered the way an add-on would
register one — which is the part a store test cannot reach, because the whole
question is whether the dependency wiring carries the principal to the query.

The resolver here reads a header. A real one would read a session or introspect
a token; the engine cannot tell the difference and that is the point of the
seam.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from cadless.identity import LOCAL, SYSTEM_KEY, Principal, register_principal_resolver
from cadless.identity import unregister_principal_resolver as _unregister
from cadless.store import Store

HEADER = "X-Test-Principal"


def run(coro):
    return asyncio.run(coro)


def _by_header(request):
    """A throwaway plugin's idea of identity."""
    return Principal(request.headers.get(HEADER, "anonymous"))


@pytest.fixture
def hosted(tmp_path):
    """A build hosting two people, with one item that shipped with it."""
    store = Store(db_path=tmp_path / "db.sqlite", artifacts_dir=tmp_path / "artifacts")

    async def seed():
        await store.init()
        a = await store.create_project("A's bracket", owner="user-a")
        av = await store.add_version(a.id, "make it", "code", True, owner="user-a")
        blob = tmp_path / "a.step"
        blob.write_text("a-geometry")
        await store.add_artifact(av.id, "step", str(blob), owner="user-a")
        b = await store.create_project("B's shelf", owner="user-b")
        shipped = await store.create_project(
            "Demo house", catalog_item_id="house/demo", owner=SYSTEM_KEY
        )
        return a.id, av.id, b.id, shipped.id

    ids = run(seed())
    register_principal_resolver(_by_header)
    app = create_app(store=store)
    try:
        with TestClient(app) as client:
            yield client, ids
    finally:
        _unregister()


def _as(client, who):
    return {HEADER: who}


def test_a_listing_shows_only_your_own_and_what_shipped(hosted):
    client, (a_id, _av, b_id, shipped_id) = hosted

    seen_by_a = {p["id"] for p in client.get("/projects", headers=_as(client, "user-a")).json()}
    seen_by_b = {p["id"] for p in client.get("/projects", headers=_as(client, "user-b")).json()}

    assert a_id in seen_by_a and b_id not in seen_by_a
    assert b_id in seen_by_b and a_id not in seen_by_b
    # What came with the build is there for both of them.
    assert shipped_id in seen_by_a and shipped_id in seen_by_b


def test_fetching_another_persons_project_is_a_404(hosted):
    client, (_a, _av, b_id, _s) = hosted
    assert client.get(f"/projects/{b_id}", headers=_as(client, "user-a")).status_code == 404


def test_renaming_another_persons_project_is_a_404(hosted):
    client, (_a, _av, b_id, _s) = hosted
    r = client.patch(f"/projects/{b_id}", json={"name": "taken"}, headers=_as(client, "user-a"))
    assert r.status_code == 404
    still = client.get(f"/projects/{b_id}", headers=_as(client, "user-b")).json()
    assert still["name"] == "B's shelf"


def test_deleting_another_persons_project_is_a_404(hosted):
    client, (_a, _av, b_id, _s) = hosted
    assert client.delete(f"/projects/{b_id}", headers=_as(client, "user-a")).status_code == 404
    assert client.get(f"/projects/{b_id}", headers=_as(client, "user-b")).status_code == 200


def test_downloading_another_persons_geometry_is_a_404(hosted):
    """The narrowest leak on the API: file bytes addressed by a bare integer."""
    client, (_a, a_version, _b, _s) = hosted

    mine = client.get(f"/versions/{a_version}/artifacts/step", headers=_as(client, "user-a"))
    assert mine.status_code == 200
    assert mine.text == "a-geometry"

    theirs = client.get(f"/versions/{a_version}/artifacts/step", headers=_as(client, "user-b"))
    assert theirs.status_code == 404


def test_reading_another_persons_version_is_a_404(hosted):
    client, (_a, a_version, _b, _s) = hosted
    assert client.get(f"/versions/{a_version}", headers=_as(client, "user-a")).status_code == 200
    assert client.get(f"/versions/{a_version}", headers=_as(client, "user-b")).status_code == 404


def test_a_project_created_over_http_belongs_to_whoever_asked(hosted):
    client, _ids = hosted
    made = client.post("/projects", json={"name": "mine"}, headers=_as(client, "user-c")).json()

    assert made["id"] in {
        p["id"] for p in client.get("/projects", headers=_as(client, "user-c")).json()
    }
    assert made["id"] not in {
        p["id"] for p in client.get("/projects", headers=_as(client, "user-a")).json()
    }


def test_the_response_never_carries_the_owner_key(hosted):
    # A client is only shown its own projects, so the key adds nothing it did
    # not assume — and naming other principals' key space is not the engine's
    # to do.
    client, _ids = hosted
    body = client.get("/projects", headers=_as(client, "user-a")).json()
    assert body
    assert all("owner" not in project for project in body)


def test_cloning_what_shipped_gives_you_your_own_copy(hosted):
    client, (_a, _av, _b, shipped_id) = hosted
    clone = client.post(f"/projects/{shipped_id}/clone", json={}, headers=_as(client, "user-a"))
    assert clone.status_code in (200, 201)
    made = clone.json()

    assert made["id"] in {
        p["id"] for p in client.get("/projects", headers=_as(client, "user-a")).json()
    }
    # It is theirs, so B does not have it.
    assert made["id"] not in {
        p["id"] for p in client.get("/projects", headers=_as(client, "user-b")).json()
    }
    assert made["is_catalog"] is False, "a copy is editable, so it is not the catalogue item"


# ---- the unhosted build, which is what most people run ----------------------


def test_with_no_resolver_registered_nothing_changes(tmp_path):
    """The local build: one user, no sign-in, and everything still listed.

    The app autoloads the bundled catalogue at startup, so the listing holds
    more than what is seeded here. That is the behaviour under test rather than
    noise around it — those items are filed under the build, and the point is
    that the single local user reads them exactly as before.
    """
    store = Store(db_path=tmp_path / "db.sqlite", artifacts_dir=tmp_path / "artifacts")

    async def seed():
        await store.init()
        # Written the way a local build writes them, with no owner argument.
        for name in ("Bracket", "Shelf", "Widget"):
            await store.create_project(name)

    run(seed())
    with TestClient(create_app(store=store)) as client:
        body = client.get("/projects").json()

    names = [p["name"] for p in body]
    assert {"Bracket", "Shelf", "Widget"} <= set(names)

    async def stored():
        return await store.list_projects()

    # Nothing in the database is hidden from the local user — the listing and
    # the table agree, which is the acceptance criterion in one line.
    assert len(body) == len(run(stored()))


def test_the_local_default_needs_no_header_and_no_configuration(tmp_path):
    store = Store(db_path=tmp_path / "db.sqlite", artifacts_dir=tmp_path / "artifacts")

    async def seed():
        await store.init()

    run(seed())
    with TestClient(create_app(store=store)) as client:
        made = client.post("/projects", json={"name": "mine"}).json()
        listed = client.get("/projects").json()

    assert made["id"] in {p["id"] for p in listed}

    async def owner_of():
        return (await store.get_project(made["id"])).owner

    assert run(owner_of()) == LOCAL.key


def test_a_resolver_that_fails_refuses_rather_than_falling_back(tmp_path):
    """The failure that would otherwise be silent and total."""
    store = Store(db_path=tmp_path / "db.sqlite", artifacts_dir=tmp_path / "artifacts")

    async def seed():
        await store.init()
        await store.create_project("Someone's work", owner="user-a")

    run(seed())

    def broken(_request):
        raise RuntimeError("the token service is down")

    register_principal_resolver(broken)
    try:
        with TestClient(create_app(store=store)) as client:
            r = client.get("/projects")
    finally:
        _unregister()

    # Not 200 with the local user's view of the database, which is what a
    # fallback would have produced: everyone reading everyone else's work while
    # the app answered normally.
    assert r.status_code == 503


def test_an_async_resolver_is_awaited(tmp_path):
    """The guide promises this, so it is held.

    A build that has to introspect a token cannot answer synchronously, and a
    resolver returning a coroutine that was never awaited would fail
    `check_principal` in a way that reads as the build's fault rather than ours.
    """
    store = Store(db_path=tmp_path / "db.sqlite", artifacts_dir=tmp_path / "artifacts")

    async def seed():
        await store.init()
        await store.create_project("A's work", owner="user-a")
        await store.create_project("B's work", owner="user-b")

    run(seed())

    async def slow(request):
        await asyncio.sleep(0)  # a round trip, in miniature
        return Principal(request.headers.get(HEADER, "anonymous"))

    register_principal_resolver(slow)
    try:
        with TestClient(create_app(store=store)) as client:
            listed = client.get("/projects", headers={HEADER: "user-a"}).json()
    finally:
        _unregister()

    names = {p["name"] for p in listed}
    # The bundled catalogue is in here too, filed under the build and read by
    # everybody — what matters is that the await happened and scoped correctly.
    assert "A's work" in names
    assert "B's work" not in names


def test_a_resolver_claiming_a_reserved_key_is_refused(tmp_path):
    store = Store(db_path=tmp_path / "db.sqlite", artifacts_dir=tmp_path / "artifacts")

    async def seed():
        await store.init()
        await store.create_project("What shipped", catalog_item_id="house/demo", owner=SYSTEM_KEY)

    run(seed())

    def impersonator(_request):
        return Principal(SYSTEM_KEY)

    register_principal_resolver(impersonator)
    try:
        with TestClient(create_app(store=store)) as client:
            r = client.get("/projects")
    finally:
        _unregister()

    assert r.status_code == 503


def test_artifacts_on_disk_are_untouched_by_a_refused_read(hosted, tmp_path):
    # A 404 must not be a delete. Cheap to assert and expensive to get wrong.
    client, (_a, a_version, _b, _s) = hosted
    r = client.get(f"/versions/{a_version}/artifacts/step", headers=_as(client, "user-b"))
    # Asserted, not assumed: without this the test passes on a 200 too, which is
    # exactly the state it exists to rule out.
    assert r.status_code == 404
    assert Path(tmp_path / "a.step").exists()
