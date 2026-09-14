"""The installed vendor SDKs still accept every key the adapters send.

Every other provider test injects a fake through the ``client=`` constructor
argument, so the real SDK is never imported and the suite cannot see a vendor
renaming or removing a parameter. Those tests assert the shape we *send*;
nothing asserted the shape the SDK *accepts*, which is how an unbounded pin let
``anthropic`` 1.x reach a build and take out every chat turn while CI stayed
green.

This module reads the **installed** SDK's own accepted-parameter set and
compares it with the body the adapter actually builds. It needs no network and
no credentials: an unexpected keyword fails at Python argument binding, before
a request is ever made. It therefore carries none of the live-API markers, so
the default ``-m "not bedrock and not anthropic and not openai"`` run includes
it.

Each body is built at its *maximal* shape — tools, a constrained tool choice,
extended thinking, stop sequences and a system prompt all present — because a
key the adapter emits only sometimes is exactly the one a minimal body would
miss.
"""

from __future__ import annotations

import inspect

import botocore.session

from cadless.config import Settings
from cadless.llm.providers.anthropic import AnthropicChatProvider
from cadless.llm.providers.bedrock import BedrockChatProvider
from cadless.llm.providers.openai import OpenAIChatProvider
from cadless.llm.types import ToolDef, TurnParams

# Every optional branch of the three ``_build_request`` methods turned on at
# once, so the body carries every key the adapter can ever emit.
_MAXIMAL = TurnParams(
    max_tokens=256,
    temperature=0.3,
    thinking=True,
    thinking_budget_tokens=1024,
    tool_choice="any",
    stop_sequences=["STOP"],
)
_TOOLS = [ToolDef(name="emit_part", description="write a part", input_schema={"type": "object"})]
_SYSTEM = "You are a CAD assistant."


def _body(provider, model: str) -> dict:
    return provider._build_request(
        model=model, system=_SYSTEM, messages=[], tools=_TOOLS, params=_MAXIMAL
    )


def _assert_accepted(body: dict, accepted, *, sdk: str) -> None:
    unaccepted = sorted(set(body) - set(accepted))
    assert not unaccepted, (
        f"{sdk} no longer accepts {unaccepted}, which the adapter still sends. "
        f"Either the pin let a breaking release in, or the adapter needs porting."
    )


def test_anthropic_sdk_accepts_every_key_the_adapter_sends():
    from anthropic.resources.messages import Messages

    body = _body(AnthropicChatProvider(client=object()), "sonnet-4-6")
    _assert_accepted(body, inspect.signature(Messages.create).parameters, sdk="anthropic")


def test_openai_sdk_accepts_every_key_the_adapter_sends():
    from openai.resources.chat.completions import Completions

    body = _body(OpenAIChatProvider(client=object()), "gpt-4.1")
    _assert_accepted(body, inspect.signature(Completions.create).parameters, sdk="openai")


def test_bedrock_converse_stream_accepts_every_key_the_adapter_sends():
    # The service model is read straight from botocore's bundled definitions, so
    # this needs no client, no region configuration and no credentials.
    service = botocore.session.get_session().get_service_model("bedrock-runtime")
    body = _body(BedrockChatProvider(), "sonnet-4-6")
    top = service.operation_model("ConverseStream").input_shape.members
    _assert_accepted(body, top, sdk="boto3 bedrock-runtime ConverseStream")

    # The nested level is where this adapter's sampling controls actually sit —
    # temperature is inside inferenceConfig, which is the same parameter whose
    # removal from the anthropic SDK is why this file exists. A top-level check
    # would stay green straight through AWS dropping it.
    for nested in ("inferenceConfig", "toolConfig"):
        if nested in body:
            _assert_accepted(
                body[nested],
                top[nested].members,
                sdk=f"boto3 bedrock-runtime ConverseStream.{nested}",
            )


def test_openai_embeddings_accepts_every_key_the_adapter_sends():
    """The fourth vendor call site, and the only one not on the turn path.

    ``embed`` builds its keyword arguments inline rather than through a request
    builder, so they are captured by driving the real method against a recording
    client instead of being restated here — a restated set would stop matching
    the day someone adds an argument.
    """
    from openai.resources.embeddings import Embeddings

    class _Recorder:
        def __init__(self):
            self.kwargs: dict = {}

        def create(self, **kwargs):
            self.kwargs = kwargs
            return {"data": []}

    recorder = _Recorder()
    client = type("_Client", (), {"embeddings": recorder})()
    # A text-embedding-3 model is the branch that also sends ``dimensions``, so
    # this exercises the widest set the method can produce.
    config = Settings(openai_embed_model="text-embedding-3-small")
    OpenAIChatProvider(config=config, client=client).embed("a bracket")

    assert recorder.kwargs, "embed() sent nothing"
    _assert_accepted(recorder.kwargs, inspect.signature(Embeddings.create).parameters, sdk="openai")


def test_sdk_clients_still_take_the_max_retries_argument():
    """The adapters pass ``max_retries`` to both vendor constructors.

    Those constructors run only when a real client is built, which no offline
    test does, so a vendor dropping the argument would reach a user first.
    Read from the signature rather than by constructing, so no API key is
    needed.
    """
    import anthropic
    import openai

    for ctor in (anthropic.Anthropic, openai.OpenAI):
        assert "max_retries" in inspect.signature(ctor.__init__).parameters, (
            f"{ctor.__module__}.{ctor.__qualname__} no longer takes max_retries"
        )
