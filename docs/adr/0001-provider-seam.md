# ADR-0001: Bring-your-own-key provider seam behind a neutral protocol

## Status

Accepted.

## Context

The engine needs frontier-model quality for code generation, but shipping an
open tool means we cannot assume any particular vendor account, and we do not
want to proxy anyone's traffic or hold anyone's keys. Generation quality for
build123d also differs by model, so users need the freedom to pick.

## Decision

- Users bring their own API key. The tool talks **directly** to the provider
  the user configures; there is no relay service.
- All engine code speaks one seam: the `ChatProvider` protocol
  (`cadless/llm/provider.py`) with four methods — `stream_turn`,
  `capabilities`, `complete`, `embed` — over the neutral message/tool/stream
  types in `cadless/llm/types.py`.
- Adapters live in `cadless/llm/providers/` (`bedrock`, `anthropic`,
  `openai`, plus a deterministic `fake` for offline tests) and register
  themselves with `register_provider(name, factory)`;
  `build_provider(name=None, *, settings)` resolves the configured one
  (`CADLESS_LLM_PROVIDER`).
- Vendor SDKs are imported **only** inside the adapter modules. The agent,
  pipeline and prompts never see them.
- Models are configured by role, not hard-coded: an orchestrator model for
  the conversational agent, a codegen model for the pipeline, and a fast
  model for cheap auxiliary calls (`cadless/config.py`).

## Consequences

- Adding a provider is a bounded exercise: implement the protocol, call
  `register_provider`, done — the four bundled adapters are the reference
  implementations.
- The `fake` provider makes the whole loop testable in CI with no
  credentials and no network.
- Capability differences between vendors must be expressed through the
  protocol (`capabilities()`, typed signals like `EmbeddingsUnsupported` —
  see [ADR-0002](./0002-embeddings-are-additive.md)) rather than leaking
  vendor branches into engine code.
- Keys are stored locally by `cadless/user_settings.py`, which is why the
  stack is loopback-only ([ADR-0004](./0004-local-first.md)).
