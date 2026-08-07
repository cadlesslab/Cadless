"""Config + model-profile tests.

The Bedrock Converse client itself now lives in ``cadless.llm.providers.bedrock``
(``BedrockChatProvider``) and is covered by ``tests/test_llm_providers.py``. These
tests stay focused on slug -> inference-profile resolution and ``Settings`` wiring,
which are vendor-API-independent.
"""

import pytest

from cadless.config import Settings
from cadless.model_profiles import PROFILES, resolve_model_id


def test_resolve_known_slug():
    assert resolve_model_id("sonnet-4-6") == "us.anthropic.claude-sonnet-4-6"
    assert "haiku-4-5" in PROFILES


def test_resolve_unknown_slug_raises():
    with pytest.raises(KeyError):
        resolve_model_id("gpt-9")


def test_settings_defaults_and_id_resolution():
    s = Settings()
    assert s.aws_region == "us-east-1"
    assert s.bedrock_model_slug == "sonnet-4-6"
    assert s.bedrock_model_id == "us.anthropic.claude-sonnet-4-6"
    assert s.bedrock_fast_model_id == PROFILES["haiku-4-5"]


def test_settings_env_override(monkeypatch):
    monkeypatch.setenv("CADLESS_BEDROCK_MODEL_SLUG", "opus-4-8")
    monkeypatch.setenv("CADLESS_AWS_REGION", "us-west-2")
    s = Settings()
    assert s.bedrock_model_id == "us.anthropic.claude-opus-4-8"
    assert s.aws_region == "us-west-2"


def test_bad_slug_fails_fast_on_access(monkeypatch):
    monkeypatch.setenv("CADLESS_BEDROCK_MODEL_SLUG", "nope-1")
    s = Settings()
    with pytest.raises(KeyError):
        _ = s.bedrock_model_id
