"""Settings API tests.

Exercises GET/POST /settings through the real FastAPI app: masked reads, a
save→apply→persist round-trip, and the openai/unknown-provider 400s. Offline —
no live markers, keys are fixtures.
"""

from __future__ import annotations

import json
import os

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from cadless import user_settings
from cadless.config import settings
from cadless.store import Store

_MANAGED_ATTRS = (
    "llm_provider",
    "orchestrator_model",
    "codegen_model",
    "aws_region",
    # Tuning knobs share the singleton, so they need the same save/restore.
    "rag_top_k",
    "rag_similarity_floor",
    "rag_require_tag_overlap",
    "bedrock_temperature",
    # Restored for the same reason as in test_user_settings.py: the settings
    # singleton outlives a test, so an unrestored knob is a cross-file leak.
    "vlm_critique_enabled",
    "forge_enabled",
    "forge_candidate_count",
)
_MANAGED_ENV = (
    "CADLESS_LLM_PROVIDER",
    "CADLESS_ORCHESTRATOR_MODEL",
    "CADLESS_CODEGEN_MODEL",
    "CADLESS_AWS_REGION",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
)


@pytest.fixture(autouse=True)
def isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(user_settings, "_ENV_AT_START", frozenset())
    saved_attrs = {a: getattr(settings, a) for a in _MANAGED_ATTRS}
    saved_env = {k: os.environ.get(k) for k in _MANAGED_ENV}
    yield
    for a, v in saved_attrs.items():
        setattr(settings, a, v)
    for k, v in saved_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


@pytest.fixture
def client(tmp_path):
    store = Store(db_path=tmp_path / "db.sqlite", artifacts_dir=tmp_path / "artifacts")
    with TestClient(create_app(store=store)) as c:
        yield c


def test_get_returns_masked_defaults(client):
    r = client.get("/settings")
    assert r.status_code == 200
    body = r.json()
    assert body["provider"] == "anthropic"
    assert body["providers"] == ["bedrock", "anthropic", "openai"]
    assert body["secrets"]["anthropic_api_key"]["set"] is False


def test_post_saves_applies_and_never_echoes_key(client, tmp_path):
    r = client.post("/settings", json={"provider": "anthropic", "anthropic_api_key": "sk-xyz"})
    assert r.status_code == 200
    body = r.json()
    assert body["provider"] == "anthropic"
    assert body["secrets"]["anthropic_api_key"] == {"set": True, "source": "saved"}
    assert "sk-xyz" not in r.text  # key is never echoed back
    # Persisted and applied without restart.
    assert json.loads((tmp_path / "settings.json").read_text())["provider"] == "anthropic"
    assert settings.llm_provider == "anthropic"
    # A follow-up GET reflects the change and is still masked.
    g = client.get("/settings")
    assert g.json()["secrets"]["anthropic_api_key"]["set"] is True
    assert "sk-xyz" not in g.text


def test_post_tuning_knob_round_trips_with_its_type(client):
    """A JSON number stays a number, and a false stays a set value."""
    r = client.post(
        "/settings",
        json={"rag_top_k": 7, "rag_require_tag_overlap": False, "bedrock_temperature": 0.0},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["rag_top_k"] == 7
    assert body["rag_require_tag_overlap"] is False
    assert body["rag_top_k_source"] == "saved"
    # `false` and `0.0` are instructions, not omissions — provenance must agree.
    assert body["rag_require_tag_overlap_source"] == "saved"
    assert body["bedrock_temperature_source"] == "saved"
    assert settings.rag_top_k == 7

    g = client.get("/settings").json()
    assert g["rag_top_k"] == 7
    assert g["rag_require_tag_overlap"] is False


def test_post_out_of_range_knob_returns_400(client):
    r = client.post("/settings", json={"rag_similarity_floor": 1.5})
    assert r.status_code == 400
    assert "rag_similarity_floor" in r.json()["detail"]
    # A rejected save persists nothing and applies nothing.
    assert client.get("/settings").json()["rag_similarity_floor"] == 0.55


def test_post_wrong_typed_knob_is_refused(client):
    """A non-numeric top_k is refused by the request model, not coerced."""
    r = client.post("/settings", json={"rag_top_k": "lots"})
    assert r.status_code == 422  # pydantic rejects before the handler runs
    assert client.get("/settings").json()["rag_top_k"] == 3


@pytest.mark.parametrize(
    "field",
    [
        "worker_url",  # where generated code is executed
        "exec_timeout_secs",  # the sandbox's resource limit
        "embed_dimensions",  # changing it silently invalidates every KB vector
    ],
)
def test_operator_only_field_is_refused_not_ignored(client, field):
    """Tier C is unreachable, and says so rather than accepting and discarding."""
    r = client.post("/settings", json={field: "anything"})
    assert r.status_code == 422
    assert field in r.text


def test_cost_multiplying_knob_returns_400_without_the_gate(client):
    r = client.post("/settings", json={"forge_enabled": True})
    assert r.status_code == 400
    assert "CADLESS_SETTINGS_ADVANCED" in r.json()["detail"]
    assert settings.forge_enabled is False


def test_openai_without_model_repoint_returns_400(client):
    r = client.post("/settings", json={"provider": "openai"})
    assert r.status_code == 400
    assert "CADLESS_ORCHESTRATOR_MODEL" in r.json()["detail"]
    # A rejected save persists nothing.
    assert client.get("/settings").json()["provider"] == "anthropic"


def test_openai_with_openai_models_ok(client):
    r = client.post(
        "/settings",
        json={
            "provider": "openai",
            "orchestrator_model": "gpt-4o",
            "codegen_model": "gpt-4o",
        },
    )
    assert r.status_code == 200
    assert r.json()["provider"] == "openai"


def test_unknown_provider_returns_400(client):
    r = client.post("/settings", json={"provider": "bogus"})
    assert r.status_code == 400
    assert "unknown provider" in r.json()["detail"]
