"""Credential preflight for LLM turns.

The keyless first experience must guide the user to add a key when the selected
provider has no usable credentials, instead of running a doomed LLM call that
fails with a cryptic vendor error (or, with ambient AWS creds, silently doing
nothing). Pins credential detection + the actionable chat error.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from cadless import user_settings
from cadless.config import settings
from cadless.store import Store


@pytest.fixture
def store(tmp_path):
    return Store(db_path=tmp_path / "db.sqlite", artifacts_dir=tmp_path / "arts")


@pytest.fixture
def client(store):
    with TestClient(create_app(store=store)) as c:
        yield c


def _parse_sse(text: str) -> list[dict]:
    out = []
    for ln in text.replace("\r\n", "\n").splitlines():
        if ln.startswith("data:"):
            payload = ln[5:].strip()
            if payload:
                out.append(json.loads(payload))
    return out


def test_has_credentials_anthropic(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert user_settings.has_credentials("anthropic") is False
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
    assert user_settings.has_credentials("anthropic") is True


def test_has_credentials_openai(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert user_settings.has_credentials("openai") is False
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    assert user_settings.has_credentials("openai") is True


@pytest.fixture
def no_ambient_aws(monkeypatch):
    """Only the variable under test decides the answer.

    A developer machine with a working ``~/.aws`` would make every case below
    pass for the wrong reason, leaving CI as the only place they meant anything.
    """
    for name in (
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_PROFILE",
        "AWS_BEARER_TOKEN_BEDROCK",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AWS_CONFIG_FILE", "/nonexistent/config")
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", "/nonexistent/credentials")
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")


def test_a_bedrock_bearer_token_counts_as_a_credential(no_ambient_aws, monkeypatch):
    """The form a deployment supplies, and the reason this check changed.

    botocore reads a service-specific credential at client construction, from
    ``AWS_BEARER_TOKEN_<SIGNING_NAME>``, and never through the session's SigV4
    chain. Asking the chain alone refused a correctly configured hosted build
    and told whoever was using it to open Settings and type a key in — which on
    a build serving more than one person is the thing supplying it avoids.
    """
    assert user_settings.has_credentials("bedrock") is False

    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "bedrock-api-key-x")

    assert user_settings.has_credentials("bedrock") is True


def test_an_access_key_still_counts_as_a_credential(no_ambient_aws, monkeypatch):
    """The path that already worked, asserted so the new one cannot replace it."""
    assert user_settings.has_credentials("bedrock") is False

    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAX")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")

    assert user_settings.has_credentials("bedrock") is True


def test_a_bearer_token_for_another_service_is_not_a_bedrock_credential(
    no_ambient_aws, monkeypatch
):
    """The variable is per-signing-name, so a neighbour's token must not count."""
    monkeypatch.setenv("AWS_BEARER_TOKEN_CODEWHISPERER", "not-ours")

    assert user_settings.has_credentials("bedrock") is False


def test_the_signing_name_is_the_one_botocore_will_look_for():
    """``bedrock-runtime`` is the service; ``bedrock`` is what it signs as.

    Pinned against botocore's own service model rather than trusted as a literal.
    If the two ever diverge, the variable this engine reads stops being the one
    botocore writes into, and the symptom is a deployment that silently reports
    no credentials.
    """
    import botocore.session

    model = botocore.session.get_session().get_service_model("bedrock-runtime")

    assert model.metadata["signingName"] == user_settings._BEDROCK_SIGNING_NAME


def test_a_botocore_without_bearer_support_keeps_the_credential_chain(no_ambient_aws, monkeypatch):
    """An older botocore must not lose the access-key path.

    The helper is looked up with ``getattr`` rather than imported for exactly
    this: an ImportError inside the check is caught and reported as "no
    credentials", so a `from … import` would turn a working deployment into a
    refusal on the day someone pins an older wheel.
    """
    import botocore.utils

    monkeypatch.delattr(botocore.utils, "get_token_from_environment", raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAX")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")

    assert user_settings.has_credentials("bedrock") is True


def test_credentials_hint_is_actionable():
    for provider in ("anthropic", "openai", "bedrock"):
        assert "Settings" in user_settings.credentials_hint(provider)


def test_the_bedrock_hint_names_the_form_a_deployment_supplies():
    # Whoever reads this on a hosted build may not be the operator, and cannot
    # act on "open Settings". Naming the variable gives the operator something
    # to do with the report.
    assert "AWS_BEARER_TOKEN_BEDROCK" in user_settings.credentials_hint("bedrock")


def test_chat_without_credentials_emits_actionable_error(client, monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    proj = client.post("/projects", json={"name": "p"}).json()
    r = client.post(f"/projects/{proj['id']}/chat", json={"message": "bore a hole"})
    errors = [e for e in _parse_sse(r.text) if e.get("event") == "error"]
    assert errors, r.text
    detail = errors[0]["detail"].lower()
    assert "settings" in detail
    assert "anthropic" in detail or "key" in detail
