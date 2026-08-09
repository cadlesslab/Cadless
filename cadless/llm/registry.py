"""Provider registry.

``build_provider`` resolves a :class:`~cadless.llm.provider.ChatProvider` by name
(``CADLESS_LLM_PROVIDER``; one of ``bedrock``, ``anthropic``, ``openai`` or
``fake``) using a factory table. The bundled adapters register themselves as an
import side effect of ``cadless.llm.providers``, which this module triggers
lazily so the registry itself stays vendor-free. Resolving a name that no
factory claims raises an error listing the names that are registered.

A name already taken is **refused** rather than overwritten. Which model
backend the engine generates through is not a detail a build should be able to
change by accident, and the accident has no symptom: the wrong provider answers
perfectly well. Taking a name over is still allowed, but only by asking for it
(``replace=True``).
"""

from __future__ import annotations

from collections.abc import Callable

from cadless.config import Settings
from cadless.config import settings as default_settings
from cadless.llm.provider import ChatProvider

# name -> factory(settings) -> ChatProvider
ProviderFactory = Callable[[Settings], ChatProvider]

_PROVIDER_FACTORIES: dict[str, ProviderFactory] = {}

# Whether the bundled adapters have been imported. See _load_bundled_providers.
_BUNDLED_LOADED = False


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

    ``_BUNDLED_LOADED`` is set *before* the import rather than after, and that
    order is load-bearing. ``register_provider`` calls this function, and the
    bundled adapters call ``register_provider`` while this very import is
    running; setting the flag first makes that re-entry a no-op instead of a
    second pass over a half-initialised module.
    """
    global _BUNDLED_LOADED
    if _BUNDLED_LOADED:
        return
    _BUNDLED_LOADED = True
    import cadless.llm.providers  # noqa: F401  (registers bedrock/anthropic/openai/fake)


def available_providers() -> list[str]:
    """Sorted list of registered provider names."""
    _load_bundled_providers()
    return sorted(_PROVIDER_FACTORIES)


def build_provider(name: str | None = None, *, settings: Settings | None = None) -> ChatProvider:
    """Build the configured provider.

    ``name`` defaults to ``settings.llm_provider`` (env ``CADLESS_LLM_PROVIDER``).
    Raises :class:`ValueError` with the known names on an unregistered provider.
    """
    cfg = settings or default_settings
    resolved = (name or cfg.llm_provider).lower()
    _load_bundled_providers()
    factory = _PROVIDER_FACTORIES.get(resolved)
    if factory is None:
        known = available_providers()
        known_str = ", ".join(known) if known else "(none registered yet)"
        raise ValueError(f"unknown LLM provider {resolved!r}; registered providers: {known_str}")
    return factory(cfg)
