"""Who is asking: identity is a seam, not a sign-in.

This engine has no accounts and does not want any. What it needs is a way to be
*told* who is asking, so a build hosting it for several people can keep their
work apart, while the tool on one person's machine carries on as it always has
with nothing to configure. The line this file draws is the whole point: the
engine learns *that* there is a principal and what to file rows under, and never
learns *how* anyone proved it — no tokens, no cookies, no providers appear here
or in anything this module hands to the store.

A build that hosts this registers one resolver, the same way it registers a
router or an origin. The registry refuses a second registration by default,
which matters more here than it does for an origin: two add-ons disagreeing
about who the caller is would not be a display bug, it would be a build that
answers the security question twice and uses whichever answer registered last.

Three owner values are not principals and cannot be produced by a resolver:

``SYSTEM_KEY``
    Rows the build itself owns rather than any person — the bundled catalogue,
    everything loaded before a request has ever arrived. Every principal *reads*
    them; no principal writes them, because :func:`writable_owners` is narrower
    than :func:`visible_owners` by exactly that entry.

    Something still writes them, or the catalogue could never be loaded: the
    loader widens to the build's own view by name. What that leaves open is not
    which rows can be written but **who may ask for the widening** — the three
    routes that receive or remove a catalogue item do so on behalf of whoever
    called them, and this engine has no notion of privilege to tell one caller
    from another. A hosted build that admits untrusted callers must gate or
    replace those routes; see ADR-0006.
``LOCAL``
    The single user of an unhosted build. It carries a reserved key so that a
    hosted build cannot mint a principal that collides with rows a local build
    wrote earlier — the migration case where one database is carried from one
    to the other.
``UNSCOPED``
    Not an owner at all but the absence of a restriction, for the callers that
    have no request to resolve: startup, the catalogue CLI, engine internals.
    It is a sentinel object rather than ``None`` on purpose. ``None`` is what an
    omitted argument looks like, and an omitted argument that quietly means
    "show everything" is the failure this seam exists to prevent.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Final

# Keys the engine reserves for itself. A resolver returning one of these is
# refused rather than trusted, because both of them read rows a caller has no
# business claiming to be. Spelled with a prefix rather than a control
# character so the value stays greppable in a database and survives every tool
# that would truncate at a NUL.
RESERVED_PREFIX: Final = "cadless:"

# Rows that belong to the build rather than to a person. Readable by every
# principal, writable by none of them.
SYSTEM_KEY: Final = "cadless:system"

# How long an owner key may be. Generous enough for a UUID, an email or a
# namespaced platform id, short enough that it cannot be used to smuggle a
# payload into a column every query filters on.
MAX_KEY_LENGTH: Final = 200


@dataclass(frozen=True, slots=True)
class Principal:
    """Who is asking, as far as this engine is ever told.

    ``key`` is opaque and is compared only for equality — it is what rows are
    filed under and what queries filter on. The engine never parses it, so a
    build may use a user id, a handle, a tenant, or anything else it can produce
    consistently for the same person. It must be stable: change the key and the
    work filed under the old one becomes invisible rather than reassigned.

    ``label`` is for display and nothing else. Nothing filters on it.
    """

    key: str
    label: str = ""


# The one principal of a build nobody is hosting. Its key is reserved so a
# hosted build cannot produce a principal that collides with it.
LOCAL: Final = Principal("cadless:local", "This machine")


class _Unscoped:
    """The absence of an owner restriction. See ``UNSCOPED``."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "UNSCOPED"


UNSCOPED: Final = _Unscoped()

# What a store method accepts: an owner key, or the sentinel saying the caller
# is engine-internal and is not being restricted.
Owner = str | _Unscoped

# Given whatever the caller has that identifies a request, the principal it
# belongs to. The engine types the argument as ``Any`` deliberately: naming a
# request type here would make the engine depend on the web framework, and
# `cadless/` must not depend on `backend/`. May be async, because a build that
# has to introspect a token cannot answer synchronously.
PrincipalResolver = Callable[[Any], "Principal | Awaitable[Principal]"]


def visible_owners(owner: Owner) -> tuple[str, ...] | None:
    """The owner values ``owner`` may **read**, or ``None`` for no restriction.

    A principal reads its own rows and the build's; an engine-internal caller
    reads everything. Kept out of the store so that "what may this caller see"
    has one place to be audited rather than being re-derived beside every query.
    """
    if isinstance(owner, _Unscoped):
        return None
    return (owner, SYSTEM_KEY)


def writable_owners(owner: Owner) -> tuple[str, ...] | None:
    """The owner values ``owner`` may **write**, or ``None`` for no restriction.

    Narrower than :func:`visible_owners` by exactly one entry, and that entry is
    the point. Reading the build's rows is the whole reason a bundled catalogue
    item appears for everybody; *writing* them is a different act, and a rule
    that answered both questions with one list would let any principal rename or
    delete what every other principal reads.

    Today that is refused a second time higher up — a catalogue project turns
    away mutations at the router. But that guard has to be remembered at each
    route, which is the shape of failure this seam exists to remove, and it says
    nothing about a build-owned row that is not a catalogue item. Deciding it
    here instead makes it true of every write, including the ones nobody has
    written yet.

    Widening back to the build is still possible and still deliberate: a caller
    that genuinely acts for the installation asks for that view by name.
    """
    if isinstance(owner, _Unscoped):
        return None
    return (owner,)


def acting_owner(owner: Owner) -> str:
    """Who a row created by this caller belongs to.

    Reading and creating ask different questions of the same argument, and the
    answers differ in one place: an engine-internal caller reads everything, but
    it cannot create something owned by everything. What it creates belongs to
    the single user of an unhosted build — which is what the catalogue CLI and
    anything else running outside a request actually mean.

    Note what this deliberately is not: the *source* row's owner. Cloning a
    catalogue item has to produce something the person can edit, and inheriting
    the owner would produce another copy of the build's own read-only row.
    """
    if isinstance(owner, _Unscoped):
        return LOCAL.key
    return owner


def check_principal(value: object) -> Principal:
    """Return ``value`` if a resolver was allowed to produce it, else raise.

    Called on the way out of a resolver rather than on the way into
    :class:`Principal`, because the engine's own ``LOCAL`` holds a reserved key
    and has to remain constructible. What is refused is a *build* claiming one.
    """
    if not isinstance(value, Principal):
        raise ValueError(
            f"a principal resolver must return a Principal, not {type(value).__name__}"
        )
    key = value.key
    if not isinstance(key, str) or not key:
        raise ValueError("a principal key must be a non-empty string")
    if key.startswith(RESERVED_PREFIX):
        raise ValueError(
            f"principal key {key!r} uses the reserved {RESERVED_PREFIX!r} prefix; "
            "those keys name the engine's own rows and cannot be claimed by a build"
        )
    if len(key) > MAX_KEY_LENGTH:
        raise ValueError(f"a principal key may be at most {MAX_KEY_LENGTH} characters")
    if key != key.strip():
        # Surrounding whitespace would make two spellings of one person compare
        # unequal, which reads as data loss rather than as a bug.
        raise ValueError(f"principal key {key!r} has leading or trailing whitespace")
    return value


_RESOLVER: PrincipalResolver | None = None


def register_principal_resolver(
    resolver: PrincipalResolver, *, replace: bool = False
) -> PrincipalResolver:
    """Install the resolver that says who is asking; refuses to clobber.

    Refusing a second registration is the point. An origin registered twice
    spells a chip wrong; an identity resolved twice answers the question of who
    may read what with whichever add-on happened to import last, and neither
    add-on would be able to tell that it had lost.
    """
    global _RESOLVER
    if _RESOLVER is not None and not replace:
        raise ValueError(
            "a principal resolver is already registered; pass replace=True to take it over"
        )
    _RESOLVER = resolver
    return resolver


def unregister_principal_resolver() -> None:
    """Remove the resolver (tests registering a synthetic one clean up with this)."""
    global _RESOLVER
    _RESOLVER = None


def principal_resolver() -> PrincipalResolver | None:
    """The registered resolver, or ``None`` when this build hosts nobody."""
    return _RESOLVER


def has_principal_resolver() -> bool:
    """Whether a build has claimed the identity seam."""
    return _RESOLVER is not None
