"""How a router joins the app, and what happens when one cannot.

Two ways in, for two different situations. A module named in ``ROUTER_MODULES``
is one this tree may or may not contain, and a build missing it boots without
those routes — that is what "partial builds still boot" has always meant. An
entry point is a router that ships in a *different* distribution installed
beside this one, which no list inside this tree could name.

The difference matters in what each one does when it fails. Absent is ordinary;
advertised-and-then-unloadable is a broken install, and the app says so rather
than coming up quietly short.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

import backend.app as backend_app
from backend.app import create_app
from cadless.store import Store


def _app(tmp_path):
    store = Store(db_path=tmp_path / "db.sqlite", artifacts_dir=tmp_path / "artifacts")
    return create_app(store=store)


def _paths(app) -> set[str]:
    """The paths this app serves.

    Read out of the OpenAPI schema rather than walked out of ``app.routes``.
    ``include_router`` puts one ``_IncludedRouter`` into that list instead of
    flattening the router's routes into it, so a walk over it finds the four
    documentation routes and nothing else — and would report an app with every
    router mounted as an app with none.
    """
    return set(app.openapi()["paths"])


class _Advertised:
    """What ``entry_points`` hands back, reduced to the two things we use.

    A real ``EntryPoint`` would resolve its ``value`` by importing it, which
    would mean installing a distribution to test the seam. What the app asks of
    one is only ``load()`` and, when that fails, ``value`` to name it in the
    log.
    """

    def __init__(self, value: str, produce):
        self.value = value
        self._produce = produce

    def load(self):
        return self._produce()


def _offering(*entries):
    """A stand-in for ``entry_points`` that advertises exactly ``entries``."""

    def entry_points(*, group: str):
        assert group == backend_app.ROUTER_ENTRY_POINT_GROUP
        return list(entries)

    return entry_points


def _greeting_router() -> APIRouter:
    router = APIRouter(prefix="/advertised", tags=["advertised"])

    @router.get("/hello")
    async def hello() -> dict:
        return {"from": "an installed distribution"}

    return router


def test_a_router_module_this_build_does_not_have_is_tolerated(tmp_path, monkeypatch):
    """The whole point of the module loop: a tree without a router still boots.

    Asserted with the routes of a module that *is* present, because "the app
    was created" would also be true of one that quietly registered nothing.
    """
    monkeypatch.setattr(backend_app, "ROUTER_MODULES", ("nothing_ships_this", "packages"))

    app = _app(tmp_path)

    assert "/packages/import" in _paths(app)
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200


def test_a_router_advertised_by_an_installed_distribution_is_registered(tmp_path, monkeypatch):
    """The seam a build outside this tree uses to add routes.

    Answered through the client rather than looked up in ``app.routes``: what
    is being checked is that the router was mounted, not that a path string
    reached a list.
    """
    monkeypatch.setattr(
        backend_app,
        "entry_points",
        _offering(_Advertised("some_package.routers:router", _greeting_router)),
    )

    with TestClient(_app(tmp_path)) as client:
        response = client.get("/advertised/hello")

    assert response.status_code == 200
    assert response.json() == {"from": "an installed distribution"}


def _with_lifespan(ran: list[str], *, raising: bool = False):
    """A router that records its own startup and shutdown, or fails to start."""

    def build() -> APIRouter:
        @asynccontextmanager
        async def lifespan(app):
            ran.append("startup")
            if raising:
                raise RuntimeError("the add-on blew up while starting")
            yield
            ran.append("shutdown")

        return APIRouter(prefix="/advertised", tags=["advertised"], lifespan=lifespan)

    return build


def test_an_advertised_router_brings_its_startup_work_with_it(tmp_path, monkeypatch):
    """A router carries housekeeping that has to run before requests arrive.

    A router that stages work sweeps what a killed run left behind, and once
    such a router ships separately this is the path it arrives by. Pinned here
    because the app passes an explicit ``lifespan=``, which is exactly the shape
    where a router's own could plausibly be ignored — it is not, and a future
    FastAPI that changed its mind would fail here rather than quietly stop doing
    the work.
    """
    ran: list[str] = []
    monkeypatch.setattr(
        backend_app,
        "entry_points",
        _offering(_Advertised("some_package:router", _with_lifespan(ran))),
    )

    with TestClient(_app(tmp_path)):
        pass

    assert ran == ["startup", "shutdown"]


def test_an_advertised_router_whose_startup_raises_does_not_take_the_app_down(
    tmp_path, monkeypatch, caplog
):
    """One broken add-on costs its own routes, not the whole process.

    This is the half the `try` around `load()` does not reach: `include_router`
    merges the router's lifespan into the app's, so without containment the
    add-on's failure raises out of the app's own startup and `/health` never
    answers — a liveness probe fails on a build whose only fault was installing
    one bad package.
    """
    ran: list[str] = []
    monkeypatch.setattr(
        backend_app,
        "entry_points",
        _offering(_Advertised("half_broken:router", _with_lifespan(ran, raising=True))),
    )

    with caplog.at_level(logging.ERROR, logger=backend_app.logger.name):
        with TestClient(_app(tmp_path)) as client:
            assert client.get("/health").status_code == 200
            # The routes this tree ships answer as usual beside the failed one.
            assert client.post("/packages/import").status_code != 404

    assert ran == ["startup"]
    assert "half_broken:router" in caplog.text


def test_an_advertised_router_that_cannot_be_loaded_does_not_stop_the_app(
    tmp_path, monkeypatch, caplog
):
    """A broken add-on costs its own routes and nothing else — and is reported.

    The report is the half that is easy to leave out. Without it the build is
    indistinguishable from one that never advertised a router at all, and the
    symptom is a 404 on a route someone installed a package to get.
    """

    def unloadable():
        raise ModuleNotFoundError("no module named 'half_installed'")

    monkeypatch.setattr(
        backend_app,
        "entry_points",
        _offering(_Advertised("half_installed.routers:router", unloadable)),
    )

    with caplog.at_level(logging.ERROR, logger=backend_app.logger.name):
        app = _app(tmp_path)
        with TestClient(app) as client:
            assert client.get("/health").status_code == 200

    assert "/advertised/hello" not in _paths(app)
    # The routes this tree ships are untouched by the one that failed.
    assert "/packages/import" in _paths(app)
    assert "half_installed.routers:router" in caplog.text


@pytest.mark.parametrize("advertised", [(), None])
def test_the_ordinary_build_advertises_nothing_and_is_unaffected(tmp_path, monkeypatch, advertised):
    """Nothing installed beside this one is the normal case, in both shapes.

    ``entry_points`` answers an empty sequence for a group nobody registered,
    and this pins that the seam adds nothing when it is empty rather than, say,
    depending on a router having been advertised.
    """
    if advertised is not None:
        monkeypatch.setattr(backend_app, "entry_points", _offering(*advertised))

    app = _app(tmp_path)

    assert "/packages/import" in _paths(app)
    assert "/health" in _paths(app)
