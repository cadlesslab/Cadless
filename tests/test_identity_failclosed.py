"""A hosted build that cannot say who is asking must not serve anybody.

The failure this guards against is quiet by construction. `_register_routers`
contains an add-on's startup failure so one broken extra cannot take the app
down — right for a router, and for identity it means an add-on whose sign-in
failed to register leaves the engine answering "the one local user" to
everyone. Nothing raises. The app is healthy. Every principal is reading every
other principal's work.

So a deployment says at launch that identity is not optional, and then absence
is a refusal rather than a downgrade.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.app import IdentityUnavailable, create_app
from backend.routers.settings import SettingsUpdate
from cadless.config import settings
from cadless.identity import Principal, register_principal_resolver
from cadless.identity import unregister_principal_resolver as _unregister
from cadless.store import Store


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _clean_registry():
    _unregister()
    yield
    _unregister()


@pytest.fixture
def store(tmp_path):
    s = Store(db_path=tmp_path / "db.sqlite", artifacts_dir=tmp_path / "artifacts")
    run(s.init())
    return s


def _resolver(request):
    return Principal(request.headers.get("X-Test-Principal", "anonymous"))


def test_the_local_build_starts_with_nothing_configured(store):
    """Off by default. The tool on one machine has no resolver and wants none."""
    assert settings.require_identity is False
    with TestClient(create_app(store=store)) as client:
        assert client.get("/health").status_code == 200


def test_a_hosted_build_with_no_resolver_refuses_to_start(store, monkeypatch):
    monkeypatch.setattr(settings, "require_identity", True)
    with pytest.raises(IdentityUnavailable, match="no principal resolver"):
        create_app(store=store)


def test_a_hosted_build_with_a_resolver_starts(store, monkeypatch):
    monkeypatch.setattr(settings, "require_identity", True)
    register_principal_resolver(_resolver)
    with TestClient(create_app(store=store)) as client:
        assert client.get("/health").status_code == 200


def test_an_add_on_that_failed_to_register_does_not_downgrade_the_build(store, monkeypatch):
    """The measured shape of the failure, stated as a test.

    Registration happens while the add-on's module is imported, and
    `_register_routers` swallows that import failing. What is left is a hosted
    build with no resolver — which is exactly the state this refuses.
    """
    monkeypatch.setattr(settings, "require_identity", True)
    # Nothing registered, standing in for an add-on whose import raised and was
    # logged and contained.
    with pytest.raises(IdentityUnavailable):
        create_app(store=store)


def test_a_resolver_that_breaks_later_refuses_per_request(store, monkeypatch):
    """Boot-time presence is not a promise that it keeps working."""
    monkeypatch.setattr(settings, "require_identity", True)
    state = {"broken": False}

    def flaky(request):
        if state["broken"]:
            raise RuntimeError("the token service went away")
        return Principal("user-a")

    register_principal_resolver(flaky)
    with TestClient(create_app(store=store)) as client:
        assert client.get("/projects").status_code == 200
        state["broken"] = True
        assert client.get("/projects").status_code == 503


def test_a_resolver_that_disappears_refuses_per_request(store, monkeypatch):
    """Booting with a resolver is not a promise of still having one.

    The registry is a module global and emptying it is public API, so a hosted
    build can end up with no resolver long after `create_app` checked. Without
    this the engine would fall back to its own local principal and serve
    everybody the same view of the database.
    """
    monkeypatch.setattr(settings, "require_identity", True)
    register_principal_resolver(_resolver)
    with TestClient(create_app(store=store)) as client:
        assert client.get("/projects").status_code == 200
        _unregister()
        assert client.get("/projects").status_code == 503


def test_a_resolver_may_refuse_in_its_own_words(store, monkeypatch):
    """401 and 403 belong to the build, not to the engine.

    Turning a deliberate refusal into 503 would report the installation as
    broken when it is working exactly as intended.
    """
    monkeypatch.setattr(settings, "require_identity", True)

    def demanding(request):
        if "X-Test-Principal" not in request.headers:
            raise HTTPException(status_code=401, detail="sign in")
        raise HTTPException(status_code=403, detail="not for you")

    register_principal_resolver(demanding)
    with TestClient(create_app(store=store)) as client:
        assert client.get("/projects").status_code == 401
        assert client.get("/projects", headers={"X-Test-Principal": "a"}).status_code == 403


def test_identity_cannot_be_switched_off_through_the_settings_endpoint(store):
    """The endpoint is unauthenticated, so this must not be reachable from it.

    `SettingsUpdate` forbids unknown fields, which is what keeps the tier
    honest: a caller naming this is refused by the request model rather than
    having the value quietly discarded.
    """
    assert "require_identity" not in SettingsUpdate.model_fields

    with TestClient(create_app(store=store)) as client:
        r = client.post("/settings", json={"require_identity": False})
    assert r.status_code == 422


def test_the_refusal_names_the_switch_that_caused_it(store, monkeypatch):
    # A deployment that will not start has to be able to find out why from the
    # message alone; the process is not up to be asked anything else.
    monkeypatch.setattr(settings, "require_identity", True)
    with pytest.raises(IdentityUnavailable) as caught:
        create_app(store=store)
    assert "CADLESS_REQUIRE_IDENTITY" in str(caught.value)
