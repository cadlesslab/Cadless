"""The identity seam: the registry, and what a resolver is not allowed to say.

These are the rules a hosted build depends on being enforced *here*, before a
principal ever reaches a query. A resolver that could claim the engine's own
reserved keys would read rows belonging to the build and to every other user,
so the refusals below are the security boundary rather than input tidiness.
"""

from __future__ import annotations

import pytest

from cadless.identity import (
    LOCAL,
    MAX_KEY_LENGTH,
    RESERVED_PREFIX,
    SYSTEM_KEY,
    UNSCOPED,
    Principal,
    check_principal,
    has_principal_resolver,
    principal_resolver,
    register_principal_resolver,
    unregister_principal_resolver,
    visible_owners,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    """The registry is a module global; leave it as it was found."""
    unregister_principal_resolver()
    yield
    unregister_principal_resolver()


def _resolver(_request):
    return Principal("someone")


def test_no_resolver_is_the_ordinary_unhosted_build():
    assert has_principal_resolver() is False
    assert principal_resolver() is None


def test_registering_returns_the_resolver_and_installs_it():
    assert register_principal_resolver(_resolver) is _resolver
    assert has_principal_resolver() is True
    assert principal_resolver() is _resolver


def test_a_second_registration_is_refused():
    register_principal_resolver(_resolver)

    def other(_request):
        return Principal("other")

    with pytest.raises(ValueError, match="already registered"):
        register_principal_resolver(other)
    # The refusal must not have half-applied: the first one is still in charge.
    assert principal_resolver() is _resolver


def test_replace_takes_the_seam_over_deliberately():
    register_principal_resolver(_resolver)

    def other(_request):
        return Principal("other")

    register_principal_resolver(other, replace=True)
    assert principal_resolver() is other


def test_unregistering_returns_the_build_to_unhosted():
    register_principal_resolver(_resolver)
    unregister_principal_resolver()
    assert has_principal_resolver() is False


# ---- what a resolver may not produce ---------------------------------------


def test_check_principal_passes_an_ordinary_principal():
    p = Principal("user-42", "Ada")
    assert check_principal(p) is p


@pytest.mark.parametrize("claimed", [SYSTEM_KEY, LOCAL.key, RESERVED_PREFIX + "anything"])
def test_a_resolver_cannot_claim_a_reserved_key(claimed):
    with pytest.raises(ValueError, match="reserved"):
        check_principal(Principal(claimed))


def test_the_engines_own_local_principal_holds_a_reserved_key():
    # This is what stops a hosted build minting a principal that collides with
    # the rows an earlier local build wrote into the same database.
    assert LOCAL.key.startswith(RESERVED_PREFIX)
    assert SYSTEM_KEY.startswith(RESERVED_PREFIX)
    assert LOCAL.key != SYSTEM_KEY


def test_a_resolver_must_return_a_principal():
    with pytest.raises(ValueError, match="must return a Principal"):
        check_principal("user-42")


@pytest.mark.parametrize("key", ["", 42, None])
def test_a_key_that_is_not_a_non_empty_string_is_refused(key):
    # A resolver is a build's own code, so a non-string key is a mistake rather
    # than an attack — but it would reach a query as a bound parameter and match
    # nothing, which reads as the person's work having vanished.
    with pytest.raises(ValueError, match="non-empty"):
        check_principal(Principal(key))


def test_an_over_long_key_is_refused():
    check_principal(Principal("a" * MAX_KEY_LENGTH))
    with pytest.raises(ValueError, match="at most"):
        check_principal(Principal("a" * (MAX_KEY_LENGTH + 1)))


@pytest.mark.parametrize("key", [" user-42", "user-42 ", "\tuser-42"])
def test_surrounding_whitespace_is_refused(key):
    # Two spellings of one person would compare unequal, and the work filed
    # under the other spelling would read as missing rather than as a bug.
    with pytest.raises(ValueError, match="whitespace"):
        check_principal(Principal(key))


# ---- the visibility rule ----------------------------------------------------


def test_an_engine_internal_caller_is_not_restricted():
    assert visible_owners(UNSCOPED) is None


def test_a_principal_sees_its_own_rows_and_the_builds():
    assert visible_owners("user-42") == ("user-42", SYSTEM_KEY)


def test_the_visibility_rule_never_includes_another_principal():
    visible = visible_owners("user-42")
    assert visible is not None
    assert "user-99" not in visible
    assert LOCAL.key not in visible
