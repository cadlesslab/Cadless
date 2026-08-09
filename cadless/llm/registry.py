"""Provider registry.

``build_provider`` resolves a :class:`~cadless.llm.provider.ChatProvider` by name
(``CADLESS_LLM_PROVIDER``; one of ``bedrock``, ``anthropic``, ``openai`` or
``fake``) using a factory table. The bundled adapters register themselves as an
import side effect of ``cadless.llm.providers``, which this module triggers
lazily so the registry itself stays vendor-free. Resolving a name that no
factory claims raises an error listing the names that are registered.

A provider need not be in this tree. A distribution installed beside the engine
advertises one through the ``cadless.llm_providers`` entry-point group, and the
engine builds it like any other — so a build can generate through a backend
this repository has never heard of without editing it.

A name already taken is **refused** rather than overwritten. Which model
backend the engine generates through is not a detail a build should be able to
change by accident, and the accident has no symptom: the wrong provider answers
perfectly well. Taking a name over is still allowed, but only by asking for it
(``replace=True``).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from importlib.metadata import entry_points

from cadless.config import Settings
from cadless.config import settings as default_settings
from cadless.llm.provider import ChatProvider

# name -> factory(settings) -> ChatProvider
ProviderFactory = Callable[[Settings], ChatProvider]

_PROVIDER_FACTORIES: dict[str, ProviderFactory] = {}

# Where a distribution installed beside this one says it has a provider to add.
# The bundled adapters can only be modules inside this tree, so they are no way
# in for a provider that ships separately — and a build that adds one should not
# have to edit this file to be allowed to.
PROVIDER_ENTRY_POINT_GROUP = "cadless.llm_providers"

# Names that were advertised but whose factory could not be produced, kept so
# that selecting one reports what actually went wrong. Deliberately separate
# from the factory table: a broken name must not look available, and must not
# be confused with a name that was refused for colliding with a bundled one.
_PROVIDER_LOAD_ERRORS: dict[str, BaseException] = {}

# Discovery is cached because build_provider runs once per generation request and
# an uncached scan would walk sys.path every time. The flag is set *after* the
# work, under the lock, so a second thread blocks until the table is populated
# rather than being told the work is done and reading a table that is not.
# The lock is re-entrant, and _DISCOVERY_RUNNING is what stops that re-entrancy
# from restarting the loop: an advertised module is third-party code, and one
# that calls build_provider() while it is still being imported would otherwise
# come back round through here on the same thread.
_DISCOVERY_LOCK = threading.RLock()
_DISCOVERY_RUNNING = False
_ADVERTISED_LOADED = False

logger = logging.getLogger(__name__)


def register_provider(
    name: str, factory: ProviderFactory, *, replace: bool = False
) -> ProviderFactory:
    """Register ``factory`` under ``name`` (lowercased); refuses to clobber.

    Refusing a second registration is the point, and it is the same rule the
    identity seam applies for the same reason: two things claiming one name
    would resolve to whichever imported last, and neither would be able to tell
    that it had lost. Returns the factory, so it can be used as a decorator.
    """
    _load_bundled_providers()
    key = name.lower()
    if key in _PROVIDER_FACTORIES and not replace:
        raise ValueError(
            f"a provider named {key!r} is already registered; pass replace=True to take it over"
        )
    _PROVIDER_FACTORIES[key] = factory
    return factory


def unregister_provider(name: str) -> None:
    """Remove ``name`` from the table; a name nobody claimed is not an error.

    The pair to ``register_provider``: without it the only way to undo a
    registration is to reach into the table itself, and a registry a test can
    only add to is one whose additions outlive the test that made them.
    """
    _PROVIDER_FACTORIES.pop(name.lower(), None)


def _load_bundled_providers() -> None:
    """Import the bundled providers so their ``register_provider`` side effects run.

    Done lazily (and tolerant of optional deps) to keep this module vendor-free
    and avoid a circular import — ``providers`` imports this registry.

    Deliberately *not* guarded by a "already loaded" flag. The import statement
    is the better guard on both counts this function needs. Re-entrancy: the
    bundled adapters call ``register_provider`` — which calls this — while this
    very import is still running, and a re-entrant import returns the
    half-initialised module from ``sys.modules`` instead of recursing.
    Concurrency: a second thread arriving mid-import blocks on the per-module
    import lock until the first has finished, so it cannot see an empty table.
    A flag set before the import would break exactly that second property,
    which is measurable: the second thread is told the work is done and then
    reports every provider as unknown.
    """
    import cadless.llm.providers  # noqa: F401  (registers bedrock/anthropic/openai/fake)


def _load_advertised_providers() -> None:
    """Register the providers that installed distributions advertise.

    Contained per name, and for two different failures that must not be merged.
    A distribution that advertised a provider and then could not produce one is
    a broken install: the app carries on, but the cause is kept so that asking
    for that name says what happened rather than "unknown provider". A
    distribution that claims a name this tree already ships is a different
    thing — it is refused, the bundled adapter stays, and nothing is recorded
    against the name, or selecting it would report the collision instead of
    returning the adapter that is still perfectly well installed.

    The bundled adapters load first. That is what makes the refusal above land
    on the newcomer rather than on this tree's own.
    """
    global _ADVERTISED_LOADED, _DISCOVERY_RUNNING
    if _ADVERTISED_LOADED:
        return
    with _DISCOVERY_LOCK:
        if _ADVERTISED_LOADED or _DISCOVERY_RUNNING:
            return
        _DISCOVERY_RUNNING = True
        try:
            _discover()
        finally:
            _DISCOVERY_RUNNING = False
            _ADVERTISED_LOADED = True


def _discover() -> None:
    """The discovery loop itself. Called once, under ``_DISCOVERY_LOCK``."""
    _load_bundled_providers()
    for entry in entry_points(group=PROVIDER_ENTRY_POINT_GROUP):
        try:
            factory = entry.load()
        except (Exception, SystemExit) as exc:
            # SystemExit is not an Exception, and a module that calls sys.exit()
            # while being imported would otherwise walk straight out of here and
            # take the process with it — which is the opposite of the
            # containment this function exists to provide. KeyboardInterrupt is
            # deliberately still allowed through, so Ctrl-C keeps working.
            _PROVIDER_LOAD_ERRORS[entry.name.lower()] = exc
            logger.exception("could not load the LLM provider advertised as %s", entry.value)
            continue
        try:
            register_provider(entry.name, factory)
        except ValueError:
            logger.exception(
                "the LLM provider advertised as %s claims the name %r, which is already "
                "registered; keeping the one already in place",
                entry.value,
                entry.name,
            )


def available_providers() -> list[str]:
    """Sorted names that can actually be built.

    A distribution that advertised a provider and then could not produce one is
    absent from this list. The name exists; nothing here can return it.
    """
    _load_bundled_providers()
    _load_advertised_providers()
    return sorted(_PROVIDER_FACTORIES)


def build_provider(name: str | None = None, *, settings: Settings | None = None) -> ChatProvider:
    """Build the configured provider.

    ``name`` defaults to ``settings.llm_provider`` (env ``CADLESS_LLM_PROVIDER``).

    Raises :class:`ValueError` two ways, and the difference is the whole point
    of telling them apart: a name nothing claims is reported with the names
    that are registered, while a name some distribution advertised and then
    could not produce is reported as that, carrying the underlying failure as
    its cause. Calling the second one "unknown" would send the reader looking
    for a missing install that is in fact right there.
    """
    cfg = settings or default_settings
    resolved = (name or cfg.llm_provider).lower()
    _load_bundled_providers()
    _load_advertised_providers()
    factory = _PROVIDER_FACTORIES.get(resolved)
    if factory is None:
        failure = _PROVIDER_LOAD_ERRORS.get(resolved)
        if failure is not None:
            raise ValueError(
                f"the LLM provider {resolved!r} is installed but could not be loaded"
            ) from failure
        known = available_providers()
        known_str = ", ".join(known) if known else "(none registered yet)"
        raise ValueError(f"unknown LLM provider {resolved!r}; registered providers: {known_str}")
    return factory(cfg)
