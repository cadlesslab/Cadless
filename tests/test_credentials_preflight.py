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


def test_credentials_hint_is_actionable():
    for provider in ("anthropic", "openai", "bedrock"):
        assert "Settings" in user_settings.credentials_hint(provider)


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
