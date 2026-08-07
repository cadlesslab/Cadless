"""Where a received item came from: origins are data, not code.

Every way an item can arrive is one :class:`Origin` entry declaring the key it
is recorded under, the label a chip spells, and where it sorts among the others.
Consumers look an origin up here instead of branching on the key, so a build
that adds a way of arriving adds a single ``register_origin()`` call and no code
edits anywhere else. The same shape as ``cadless.catalog.domains``, one layer
over.

Two arrive with this engine. ``local`` is everything that did not arrive at all
— the bundled samples, work authored on this machine, and whatever catalogue a
deployment mounted in their place; the tool does not invent a rule for telling
those apart. ``file`` is a package handed over directly, on a drive or in a
chat. Anything else is registered by the build that knows about it.

An origin that can be recorded *inside* an item also brings a ``reader``: given
the item's ``source.json``, it says whether that item is one of its own. That is
what keeps the engine from having to recognise arrivals it does not implement —
a package fetched from somewhere writes its own block and its own sentence, and
the same code that writes them is the code that reads them back.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

# Unregistered keys (an item recorded by a build that is not this one) sort
# after every registered origin, exactly as an unregistered domain does.
_UNREGISTERED_SORT = float("inf")

# An origin key reaches a URL as one path segment — see `register_origin`.
_KEY_SHAPE = re.compile(r"[a-z0-9][a-z0-9_-]*")

# What this engine itself writes into a `source.json`. Anything else at the top
# level was put there by a build that knew what it meant — see `origin_of`.
ENGINE_PROVENANCE_KEYS = frozenset(
    {
        "dataset",
        "representation",
        "license",
        "id",
        "note",
        "author",
        "author_handle",
        "derived_from",
    }
)


@dataclass(frozen=True)
class ItemOrigin:
    """Where one received item came from, as the item itself records it.

    ``kind`` is the key of a registered :class:`Origin` — ``file`` for a package
    handed over directly, ``unknown`` when the item's provenance could not be
    read or was recorded by a build this one does not have. Those two are not
    the same: saying a package was handed over directly is a claim, and one this
    cannot make about an item that never said, or about an item that said
    something in a vocabulary nobody here speaks.

    The ids belong to whichever origin recorded them and are absent for an
    arrival that has none. They are what a listing is matched against; the digest
    answers *which version* of it is the copy here.

    ``licence`` is the terms the item states for itself, carried here because
    the one question that needs it — may this be passed on — is asked in the
    same breath as where the copy came from, and a second read of the same file
    to answer half of it would be a second reader to keep in step.
    """

    kind: str
    catalog_id: str | None = None
    version_id: str | None = None
    digest: str | None = None
    licence: str | None = None


# Given a `source.json` and the licence already read out of it, an item of this
# origin or nothing. Nothing means "not mine", and the next reader is asked.
OriginReader = Callable[[Mapping[str, Any], str | None], "ItemOrigin | None"]


@dataclass(frozen=True)
class Origin:
    """One way an item can arrive, described entirely as data."""

    key: str
    label: str
    # Facet and chip order (lower sorts first). The built-ins leave a gap
    # between them so a registered origin can sit in the middle without every
    # build having to renumber.
    sort_order: int = 100
    # How an item of this origin recognises itself in a `source.json`. ``None``
    # for an origin that is never recorded inside an item — `local` is decided
    # by the item's absence from the received walk, not by anything it says.
    reader: OriginReader | None = None


_REGISTRY: dict[str, Origin] = {}


def register_origin(origin: Origin, *, replace: bool = False) -> Origin:
    """Add an origin to the registry; refuses to clobber unless ``replace``.

    The key has to be one addressable path segment, because it becomes one:
    `/catalog/origins/{kind}` is how a panel asks which items it already holds
    from this origin. A key with a slash in it is not rejected by the router, it
    simply never matches — the build that registered it would find an origin
    that labels correctly and answers nothing, with no error anywhere. Refusing
    here means finding out at registration instead.
    """
    if not _KEY_SHAPE.fullmatch(origin.key):
        raise ValueError(
            f"origin key {origin.key!r} is not a single addressable path segment; "
            f"it must match {_KEY_SHAPE.pattern}"
        )
    if origin.key in _REGISTRY and not replace:
        raise ValueError(f"origin {origin.key!r} is already registered")
    _REGISTRY[origin.key] = origin
    return origin


def unregister_origin(key: str) -> None:
    """Remove an origin (tests registering synthetic origins clean up with this)."""
    _REGISTRY.pop(key, None)


def get_origin(key: str) -> Origin:
    """The registered origin for ``key``; raises ``ValueError`` if unknown."""
    try:
        return _REGISTRY[key]
    except KeyError:
        raise ValueError(
            f"unknown item origin {key!r}; registered: {', '.join(sorted(_REGISTRY))}"
        ) from None


def find_origin(key: str) -> Origin | None:
    """The registered origin for ``key``, or ``None`` if unknown."""
    return _REGISTRY.get(key)


def all_origins() -> list[Origin]:
    """All registered origins in UI order (sort_order, then key)."""
    return sorted(_REGISTRY.values(), key=lambda o: (o.sort_order, o.key))


def origin_label(key: str) -> str:
    """UI label for an origin key; capitalizes unregistered (foreign) keys."""
    origin = _REGISTRY.get(key)
    return origin.label if origin else key.capitalize()


def origin_sort_key(key: str) -> tuple[float, str]:
    """Sort key for UI grouping; unregistered keys sort last, then by key."""
    origin = _REGISTRY.get(key)
    return (origin.sort_order if origin else _UNREGISTERED_SORT, key)


def origin_of(provenance: Mapping[str, Any] | None) -> ItemOrigin:
    """Read an item's ``source.json`` for where the copy came from.

    Every registered reader is offered the record, in registry order, and the
    first to claim it answers. A reader is the only thing that knows the shape
    its own arrival writes, so the engine never has to hold a vocabulary for an
    arrival it does not implement.

    What is left over is the interesting part. A record this engine wrote
    entirely by itself is a package handed over directly — ``file``. But a
    record carrying a key outside :data:`ENGINE_PROVENANCE_KEYS` was written by
    a build that knew what that key meant, and this one does not: the honest
    answer is ``unknown``, not ``file``. Calling it ``file`` would be a claim
    about how it arrived, made on the strength of not recognising it — and the
    same item would change its story depending on which build opened it.
    """
    if not isinstance(provenance, Mapping):
        return ItemOrigin("unknown")
    licence = recorded_text(provenance.get("license"))
    for origin in all_origins():
        if origin.reader is None:
            continue
        claimed = origin.reader(provenance, licence)
        if claimed is not None:
            return claimed
    # Not knowing where it came from is not a reason to withhold the terms it
    # states. The one question the licence is for — may this be passed on — is
    # asked in the same breath as where the copy came from, and answering the
    # second with "cannot tell" while hiding the first would leave a publisher
    # acknowledging an original whose terms nobody showed them.
    if not isinstance(provenance.get("dataset"), str):
        return ItemOrigin("unknown", licence=licence)
    if any(key not in ENGINE_PROVENANCE_KEYS for key in provenance):
        return ItemOrigin("unknown", licence=licence)
    return ItemOrigin("file", licence=licence)


def recorded_text(value: object) -> str | None:
    """One value out of a provenance file, or nothing when it is not one.

    These files are on the user's disk and can be hand-edited or left half
    written, and a listing must not be something one of them can take down: a
    value of the wrong type would reach the response model and answer the whole
    page with a 500 over a single item. Nothing else that reads these files
    behaves differently — an unparseable one is already ``None`` rather than an
    exception. Exported because a registered reader needs the same tolerance,
    and writing a second one would be a second answer to the same question.
    """
    return value if isinstance(value, str) else None


# ----------------------------------------------------------------------------- #
# built-in origins
# ----------------------------------------------------------------------------- #

register_origin(
    Origin(
        key="local",
        label="Local",
        sort_order=0,
    )
)
register_origin(
    Origin(
        key="file",
        label="File",
        sort_order=20,
    )
)
