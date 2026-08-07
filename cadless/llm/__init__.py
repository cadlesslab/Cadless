"""Provider-neutral LLM seam (the design notes Phase 1).

The :mod:`cadless.llm` package decouples the rest of the app from any single
LLM vendor. :mod:`~cadless.llm.types` holds vendor-free domain objects,
:mod:`~cadless.llm.provider` defines the :class:`ChatProvider` protocol, and
:mod:`~cadless.llm.registry` builds a concrete provider from config.
"""

from __future__ import annotations
