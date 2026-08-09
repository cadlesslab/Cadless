"""The provider registry: who is allowed to claim a name, and who wins a tie.

The registry decides which model backend the engine generates through, so a
second registration under a name already taken is not a tidiness question. A
build that installs a package and silently changes its model backend has no way
to notice; refusing, and making the takeover something a caller asks for by
name, is what makes that visible.

This mirrors ``tests/test_identity.py`` deliberately — the identity seam
answers the same question about the same kind of module-global registry, and
the two should not drift apart in what a collision costs.
"""

from __future__ import annotations

import pytest

from cadless.config import Settings
from cadless.llm import registry as registry_mod
from cadless.llm.registry import (
    available_providers,
    build_provider,
    register_provider,
    unregister_provider,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    """The table is a module global; leave it as it was found.

    Snapshot-and-restore rather than popping known names: a test that registers
    a name and then fails would otherwise leak it into every test after it, and
    the leak would look like the seam misbehaving.

    The bundled adapters are loaded *before* the snapshot is taken. They arrive
    lazily on the first registration, so a snapshot taken before that would be
    empty, and restoring it would wipe the four bundled names while leaving the
    "already loaded" flag set — a table no later call would ever refill.
    """
    registry_mod._load_bundled_providers()
    before = dict(registry_mod._PROVIDER_FACTORIES)
    registry_mod._ADVERTISED_LOADED = False
    registry_mod._PROVIDER_LOAD_ERRORS.clear()
    yield
    registry_mod._PROVIDER_FACTORIES.clear()
    registry_mod._PROVIDER_FACTORIES.update(before)
    registry_mod._ADVERTISED_LOADED = False
    registry_mod._PROVIDER_LOAD_ERRORS.clear()


class _Stub:
    """Stands in for a provider. ``build_provider`` returns whatever it gets."""


def _factory(_settings) -> _Stub:
    return _Stub()


def test_registering_returns_the_factory_and_installs_it():
    assert register_provider("stub", _factory) is _factory
    assert isinstance(build_provider("stub", settings=Settings()), _Stub)


def test_a_second_registration_is_refused():
    register_provider("stub", _factory)

    def other(_settings):
        return _Stub()

    with pytest.raises(ValueError, match="already registered"):
        register_provider("stub", other)
    # The refusal must not have half-applied: the first one is still in charge.
    assert registry_mod._PROVIDER_FACTORIES["stub"] is _factory


def test_the_refusal_survives_a_difference_in_case():
    """Names are stored lowercased, so the guard has to compare them that way.

    Without this the whole refusal is bypassed by capitalising a letter, which
    is exactly the accident an add-on would have.
    """
    register_provider("stub", _factory)

    with pytest.raises(ValueError, match="already registered"):
        register_provider("STUB", lambda _settings: _Stub())

    assert registry_mod._PROVIDER_FACTORIES["stub"] is _factory


def test_replace_takes_the_name_over_deliberately():
    register_provider("stub", _factory)

    def other(_settings):
        return _Stub()

    assert register_provider("stub", other, replace=True) is other
    assert registry_mod._PROVIDER_FACTORIES["stub"] is other


def test_a_bundled_name_cannot_be_taken_by_accident():
    """The case the seam exists for, asserted on a name this tree really ships."""
    with pytest.raises(ValueError, match="already registered"):
        register_provider("bedrock", _factory)

    assert build_provider("bedrock", settings=Settings()).__class__.__name__ == (
        "BedrockChatProvider"
    )


def test_unregistering_returns_the_name_to_unknown():
    register_provider("stub", _factory)
    unregister_provider("stub")

    assert "stub" not in available_providers()
    with pytest.raises(ValueError, match="unknown LLM provider"):
        build_provider("stub", settings=Settings())


def test_unregistering_a_name_nobody_claimed_is_not_an_error():
    """Cleanup runs on paths where the registration may not have happened."""
    unregister_provider("never-registered")


# ---- the seam: a provider that ships in another distribution ----------------


class _Advertised:
    """What ``entry_points`` hands back, reduced to the three things we use.

    A real ``EntryPoint`` would resolve its ``value`` by importing it, which
    would mean installing a distribution to test the seam. What the registry
    asks of one is its ``name`` (which becomes the provider name), ``load()``,
    and — when that fails — ``value`` to name it in the log.
    """

    def __init__(self, name: str, value: str, produce):
        self.name = name
        self.value = value
        self._produce = produce

    def load(self):
        return self._produce()


def _offering(*entries):
    """A stand-in for ``entry_points`` that advertises exactly ``entries``."""

    def entry_points(*, group: str):
        assert group == registry_mod.PROVIDER_ENTRY_POINT_GROUP
        return list(entries)

    return entry_points


def _advertising(monkeypatch, *entries) -> None:
    monkeypatch.setattr(registry_mod, "entry_points", _offering(*entries))


def test_a_provider_from_an_installed_distribution_is_selectable(monkeypatch):
    """The seam itself: a build adds a model backend without editing this tree."""
    _advertising(monkeypatch, _Advertised("outside", "somewhere:factory", lambda: _factory))

    assert isinstance(build_provider("outside", settings=Settings()), _Stub)
    assert "outside" in available_providers()


def test_an_advertised_provider_is_selectable_through_the_environment(monkeypatch):
    """Selection is by environment only — the settings panel keeps a closed list."""
    _advertising(monkeypatch, _Advertised("outside", "somewhere:factory", lambda: _factory))
    monkeypatch.setenv("CADLESS_LLM_PROVIDER", "outside")

    assert isinstance(build_provider(settings=Settings()), _Stub)


@pytest.mark.parametrize("advertised", [(), None])
def test_the_ordinary_build_advertises_nothing_and_is_unaffected(monkeypatch, advertised):
    """The negative control.

    Without it a loader that never ran is indistinguishable from one that ran
    and found nothing. Parametrized over an empty group *and* the real
    unpatched ``entry_points``: the first proves the loop is a no-op, and only
    the second proves an ordinary install is untouched by any of this.
    """
    if advertised is not None:
        _advertising(monkeypatch, *advertised)

    assert available_providers() == ["anthropic", "bedrock", "fake", "openai"]


def test_an_advertised_provider_cannot_displace_a_bundled_one(monkeypatch):
    """A name this tree ships stays this tree's, and the app carries on."""
    _advertising(monkeypatch, _Advertised("bedrock", "somewhere:factory", lambda: _factory))

    assert build_provider("bedrock", settings=Settings()).__class__.__name__ == (
        "BedrockChatProvider"
    )
    # The contested name must not be recorded as a failure, or selecting it
    # would report the collision instead of returning the bundled adapter.
    assert "bedrock" not in registry_mod._PROVIDER_LOAD_ERRORS


def test_a_provider_that_cannot_be_produced_does_not_stop_the_others(monkeypatch):
    """One broken add-on must not cost a build its own providers."""

    def unloadable():
        raise ModuleNotFoundError("no module named 'half_installed'")

    _advertising(
        monkeypatch,
        _Advertised("broken", "half_installed:factory", unloadable),
        _Advertised("outside", "somewhere:factory", lambda: _factory),
    )

    assert isinstance(build_provider("bedrock", settings=Settings()), object)
    # The entry after the broken one is still reached.
    assert isinstance(build_provider("outside", settings=Settings()), _Stub)


def test_selecting_a_provider_that_would_not_load_reports_why(monkeypatch):
    """ "unknown provider" would be a lie, and would send the reader hunting.

    The distribution is installed and the name is real; what failed is
    producing the factory, and that cause travels with the refusal.
    """

    def unloadable():
        raise ModuleNotFoundError("no module named 'half_installed'")

    _advertising(monkeypatch, _Advertised("broken", "half_installed:factory", unloadable))

    with pytest.raises(ValueError, match="could not be loaded") as caught:
        build_provider("broken", settings=Settings())

    assert isinstance(caught.value.__cause__, ModuleNotFoundError)
    assert "half_installed" in str(caught.value.__cause__)


def test_a_provider_that_would_not_load_is_not_offered_as_available(monkeypatch):
    """``available_providers`` means "can be built", so a broken name is absent."""

    def unloadable():
        raise ModuleNotFoundError("no module named 'half_installed'")

    _advertising(monkeypatch, _Advertised("broken", "half_installed:factory", unloadable))

    assert "broken" not in available_providers()
