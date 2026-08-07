# ADR-0002: Embeddings are additive — no provider is required to have them

## Status

Accepted.

## Context

Retrieval grounding (`cadless/rag.py`) and knowledge-base distillation
(`cadless/distill.py`) want embeddings. Not every chat provider offers an
embeddings API — Anthropic notably does not — and we did not want to bundle a
local embedding model, which would add heavyweight dependencies and a second
model-quality surface to maintain for a feature the tool must work without
anyway (the knowledge base starts empty on day one).

## Decision

- `embed()` is part of the `ChatProvider` protocol, but a provider may
  signal non-support by raising the typed `EmbeddingsUnsupported`
  (`cadless/llm/provider.py`).
- Retrieval and auto-distillation treat that signal as "skip quietly":
  `retrieve_grounding` returns an empty grounding string (the prompt is
  simply not augmented) and `auto_distill` logs at info level and stores
  nothing. Explicitly requested distillation surfaces the error, because
  someone asking for it directly deserves to know why it cannot work.
- Providers with native embeddings use them (Bedrock via Titan, OpenAI via
  its embedding models); each knowledge base stays in a single provider's
  embedding space — vectors from different models are not comparable.
- No local embedding model ships with the tool.

## Consequences

- Every provider works end to end; embedding-less providers just run the
  plain few-shot path with no retrieval. Features degrade, the tool does
  not.
- The knowledge-base flywheel (distill good turns, retrieve them later) only
  accrues on providers with embeddings.
- Mixing providers against one knowledge base is undefined by design; the
  store keys vectors to the space they were written in.
