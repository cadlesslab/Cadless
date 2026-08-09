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
    yield
    registry_mod._PROVIDER_FACTORIES.clear()
    registry_mod._PROVIDER_FACTORIES.update(before)


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
