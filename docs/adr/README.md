# Architecture decision records

Short records of the decisions that shape the engine, in the classic
Status / Context / Decision / Consequences format. Newer records supersede
older ones only when they say so explicitly.

| ADR | Decision |
|---|---|
| [0001](./0001-provider-seam.md) | Bring-your-own-key provider seam behind a neutral protocol |
| [0002](./0002-embeddings-are-additive.md) | Embeddings are additive — no provider is required to have them |
| [0003](./0003-three-layer-sandbox.md) | Three sandbox layers for generated code, and no kernel isolation |
| [0004](./0004-local-first.md) | Local-first, loopback-only, bring-your-own-key (its source-of-truth bullet is superseded by 0007) |
| [0005](./0005-best-of-n-judge.md) | Best-of-N candidates judged by a cheap-first ladder |
| [0006](./0006-identity-seam.md) | The engine is told who is asking, and never how (amends 0004) |
| [0007](./0007-engine-and-implementations.md) | This repository is the engine and its seams; what plugs into them is not published (supersedes one bullet of 0004) |
