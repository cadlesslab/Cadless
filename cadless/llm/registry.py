"""Provider registry.

``build_provider`` resolves a :class:`~cadless.llm.provider.ChatProvider` by name
(``CADLESS_LLM_PROVIDER``; one of ``bedrock``, ``anthropic``, ``openai`` or
``fake``) using a factory table. The bundled adapters register themselves as an
import side effect of ``cadless.llm.providers``, which this module triggers
lazily so the registry itself stays vendor-free. Resolving a name that no
factory claims raises an error listing the names that are registered.
"""

from __future__ import annotations

from collections.abc import Callable

from cadless.config import Settings
from cadless.config import settings as default_settings
from cadless.llm.provider import ChatProvider

# name -> factory(settings) -> ChatProvider
ProviderFactory = Callable[[Settings], ChatProvider]

_PROVIDER_FACTORIES: dict[str, ProviderFactory] = {}


def register_provider(name: str, factory: ProviderFactory) -> None:
    """Register ``factory`` under ``name`` (lowercased)."""
    _PROVIDER_FACTORIES[name.lower()] = factory


def _load_bundled_providers() -> None:
    """Import the bundled providers so their ``register_provider`` side effects run.

    Done lazily (and tolerant of optional deps) to keep this module vendor-free
    and avoid a circular import — ``providers`` imports this registry.
    """
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
