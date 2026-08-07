# Adding an LLM provider

The engine talks to models through one small protocol, so teaching it a new
backend is a bounded job: implement four methods, register a factory, add one
import. This page is the procedure. For *why* the seam exists — and why vendor
SDKs are confined to one directory — read
[ADR-0001](../adr/0001-provider-seam.md); for where the pieces sit in the wider
system, [architecture.md](../architecture.md).

Everything below is in `cadless/llm/`. The four bundled adapters (`bedrock`,
`anthropic`, `openai`, `fake`) are working reference implementations — when this
page says "the bundled adapters do X", you can read X in
`cadless/llm/providers/`.

## What you implement

`ChatProvider` in `cadless/llm/provider.py` is a `typing.Protocol`, so there is
no base class to inherit and nothing to import in order to conform. Write a
class with these four methods and you are a provider:

| Method | Signature |
| --- | --- |
| `stream_turn` | `(self, *, model: str, system: str, messages: Sequence[Message], tools: Sequence[ToolDef], params: TurnParams) -> Iterator[StreamEvent]` |
| `capabilities` | `(self, model: str) -> Capabilities` |
| `complete` | `(self, *, model: str, system: str, user: str, temperature: float \| None = None) -> str` |
| `embed` | `(self, text: str \| Sequence[str]) -> list[float] \| list[list[float]]` |

Note the keyword-only markers: `stream_turn` and `complete` take every argument
by keyword. The protocol is `@runtime_checkable`, so `isinstance(obj,
ChatProvider)` is a real check you can assert in your tests — it verifies the
methods exist, not their signatures.

`complete` runs one non-streaming turn and returns the text. `capabilities`
reports what a model supports so the caller can adapt a request rather than
guess.

## The neutral types

Providers translate a vendor's wire format to and from `cadless/llm/types.py`.
Nothing outside `cadless/llm/providers/` should ever see a vendor object.

- `Message` (`role`, `content`) and `ToolDef` (`name`, `description`,
  `input_schema`) are the request side.
- `TurnParams` carries the per-turn knobs — `max_tokens`, `temperature`,
  `thinking`, `thinking_budget_tokens`, `tool_choice`, `stop_sequences`. On the
  nullable ones, **`None` means "use the provider default"** — do not substitute
  your own. (`thinking` is a plain bool, defaulting to `False`.)
- `Capabilities` reports `supports_thinking`, `supports_tool_choice` and
  `max_output_tokens` (default `4096`).
- `StreamEvent` is the event vocabulary listed in the next section.

### One wrinkle worth knowing up front

`stream_turn` is *typed* `Iterator[StreamEvent]`, but every bundled adapter
yields `StreamChunk` — a frozen dataclass pairing a `StreamEvent` with its
payload dict, defined in `cadless/llm/providers/__init__.py`. The event is the
protocol boundary; the payload is the out-of-band data the agent loop and the
SSE layer actually consume. Yield `StreamChunk`, like the bundled adapters do.

## Translating the stream

Emit this vocabulary. The payload keys are the ones consumers expect:

| Event | Payload |
| --- | --- |
| `TURN_START` | — |
| `TEXT_DELTA` | `{"text": str}` |
| `THINKING_DELTA` | `{"text": str}` |
| `THINKING_STOP` | `{"text": str, "block": ...}` — the verbatim block |
| `TOOL_USE_START` | `{"name": str, "id": str}` |
| `TOOL_INPUT_DELTA` | `{"partial_json": str}` |
| `TOOL_USE_STOP` | `{"id", "name", "input", "block"}` |
| `TURN_DELTA` | `{"stop_reason": StopReason}` |
| `USAGE` | `{"input_tokens": int, "output_tokens": int}` |
| `TURN_STOP` | — |

Tool arguments arrive as JSON fragments. Accumulate them and finish with
`parse_partial_json` from `cadless.llm.providers`: on a turn that was cut off
mid-call it returns `{"__partial_json__": <raw>}` instead of raising, so the loop
can still inspect what was attempted. Normalise the vendor's stop reason onto
`StopReason` (`end_turn`, `tool_use`, `max_tokens`, `stop_sequence`).

Read `_translate_stream` in any of the three network-backed adapters for a
worked example. (`fake` has no such function — it replays a script.)

## Writing the adapter

Create `cadless/llm/providers/<name>.py`. The class shape is not part of the
protocol, but the three network-backed adapters share it and yours should too
(`fake` differs — it takes a canned script instead of a client):

```python
PROVIDER_NAME = "acme"


class AcmeChatProvider:
    def __init__(self, config: Settings | None = None, client=None) -> None:
        self._cfg = config or default_settings
        self._client = client  # injectable for tests; otherwise lazy-created

    @property
    def client(self):
        if self._client is None:
            import acme_sdk  # local import: no SDK dep at module import time

            self._client = acme_sdk.Client()
        return self._client
```

Two rules hide in that snippet:

1. **Import the vendor SDK inside the property, never at module top level.**
   Importing `cadless.llm.providers` imports *every* adapter, so a top-level
   `import acme_sdk` would make your dependency mandatory for everyone. The lazy
   property is what keeps the package installable and importable without it.
2. **Accept an injected `client`.** That is the seam tests use to drive the
   adapter without a network or a key.

`self._cfg` is the full `Settings` object, so any tuning knob you need is
already to hand. One caveat while you read the bundled adapters: a few
general-purpose knobs still carry `bedrock_`-prefixed names from before the seam
existed, and the non-Bedrock adapters reuse them as the neutral defaults. The
name is historical, not a hint that the setting is Bedrock-only.

## Mapping model ids

Model choice reaches you as a config slug (`CADLESS_ORCHESTRATOR_MODEL`,
`CADLESS_CODEGEN_MODEL`), not a vendor id, and translating it is **the adapter's
job** — there is no shared mapper. The bundled adapters each solve it their own
way: a private slug→id dict plus a resolver, or a validator that rejects slugs
belonging to another vendor with an explicit message. Pick whichever fits, and
fail loudly on a slug you cannot serve; a silent fallback to some default model
is the worst outcome here.

## Embeddings, or a clean refusal

`embed` is in the protocol, but embeddings are *additive* — the engine works
without them, and a provider with no embeddings API says so with a typed error:

```python
def embed(self, text: str | Sequence[str]) -> list[float] | list[list[float]]:
    raise EmbeddingsUnsupported(PROVIDER_NAME)
```

**Raise before touching the client or importing the SDK.** The callers that
treat embeddings as optional — retrieval grounding and automatic distillation —
catch `EmbeddingsUnsupported` and skip cleanly, and that skip has to work with no
credentials and no SDK installed. Callers that ask for embeddings explicitly get
the error as-is, on purpose.

If you *do* implement it: a single string returns one `list[float]`, a sequence
returns a `list[list[float]]` aligned with the input order, and the vector width
should honour `settings.embed_dimensions` so an existing knowledge base stays
readable. The rationale is in
[ADR-0002](../adr/0002-embeddings-are-additive.md).

## Registering it

Two steps. At the bottom of your module, expose a factory and register it:

```python
def _factory(settings: Settings) -> AcmeChatProvider:
    return AcmeChatProvider(config=settings)


register_provider(PROVIDER_NAME, _factory)
```

Then add your module to the import block at the bottom of
`cadless/llm/providers/__init__.py`, alongside the other four:

```python
from cadless.llm.providers import acme as _acme  # noqa: E402,F401
```

That import *is* the registration — `register_provider` runs as an import side
effect, and the registry triggers the package import lazily on first use.

### Where to import StreamChunk from

`StreamChunk` and `parse_partial_json` live in
`cadless/llm/providers/__init__.py` — the same module that imports your adapter.
All four bundled adapters therefore import them at the **bottom** of the file,
below `register_provider`:

```python
from cadless.llm.providers import (  # noqa: E402,F401
    StreamChunk,
    parse_partial_json,
)
```

A top-level import does in fact resolve today, because both names are defined
above the adapter import block in that `__init__`. The bottom placement is a
deliberate guard against that ordering ever changing, and it is what every
adapter does — follow it rather than quietly depending on definition order. The
`noqa` is there because the placement trips `E402`.

Three of the adapters *also* import `StreamChunk` inside their translate
function. That one is redundant; there is no need to copy it.

## Making it selectable

`CADLESS_LLM_PROVIDER` picks the provider; it defaults to `anthropic`.
`build_provider(name=None, *, settings=None)` resolves it, falling back to
`settings.llm_provider`, and raises `ValueError` listing the registered names if
nothing claims the name. `available_providers()` returns that list.

At this point your provider works from the environment. To make it appear in the
in-app Settings panel as well, extend `cadless/user_settings.py`:

- add the name to the `PROVIDERS` tuple — `validate()` rejects anything not in it;
- if it needs a key, map a field to its environment variable in `_SECRET_FIELDS`
  (the variable your SDK reads), and teach `has_credentials` how to detect it;
- add an entry to `_CREDENTIAL_HINTS` so a user without a key gets an actionable
  message instead of the generic fallback.

Skipping this is a legitimate choice — `fake` is deliberately registered but not
offered in the UI.

## Testing it without an API key

The suite has **no `conftest.py`**. The shared fake is a production module:
`FakeChatProvider` in `cadless/llm/providers/fake.py`, selectable with
`CADLESS_LLM_PROVIDER=fake`. It replays a scripted list of `StreamChunk`s,
records every call on `.calls`, and returns deterministic hash-derived
embeddings, so retrieval code runs offline with stable vectors.

Test modules subclass it rather than inventing new fakes — for a provider that
returns a different script per turn, one that raises mid-stream, one that
records the prompts it was given. Grep the tests for `FakeChatProvider` and copy
the closest pattern.

Your own adapter's tests split in two:

- **Offline tests** exercise translation with an injected fake client. These run
  in `make test` and should cover the stream mapping, the model-id mapping and
  the `embed` behaviour. Assert `isinstance(provider, ChatProvider)` while you
  are there.
- **Live tests** that really call the vendor get a module-level marker:

  ```python
  pytestmark = pytest.mark.acme
  ```

  Register it in `pyproject.toml` under `[tool.pytest.ini_options] markers`, or
  pytest warns on an unknown marker.

> **Do not stop at registering the marker.** `make test` and CI both select
> tests with `-m "not bedrock and not anthropic and not openai"` — a hard-coded
> list, not "every live marker". A new `acme` marker is *not* excluded, so your
> live tests would run in CI and fail without a key. Add your marker to that
> expression in **both** `Makefile` and `.github/workflows/ci.yml`.

Note that `make test` does run the `build123d` geometry tests; only live-model
tests are excluded. Expect it to take a few minutes.

## Checklist

- [ ] `cadless/llm/providers/<name>.py` with `PROVIDER_NAME` and the four methods
- [ ] Vendor SDK imported lazily inside the `client` property, never at module level
- [ ] Stream translated to `StreamChunk`s with the documented payload keys
- [ ] Config slugs mapped to vendor ids, failing loudly on an unknown slug
- [ ] `embed` implemented, or raising `EmbeddingsUnsupported` before any SDK work
- [ ] `_factory` + `register_provider` at the bottom of the module
- [ ] Import line added to `cadless/llm/providers/__init__.py`
- [ ] `StreamChunk` / `parse_partial_json` imported at the file bottom, with the `noqa`
- [ ] Offline tests pass under `make test`; live tests marked, registered in
      `pyproject.toml`, and excluded in both `Makefile` and the CI workflow
- [ ] `make lint` clean, and commits signed off (`git commit -s`) per
      [CONTRIBUTING](../../CONTRIBUTING.md)
