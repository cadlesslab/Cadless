"""Guard: the code-default LLM provider and .env.example must not silently diverge.

The provider default is declared in three spots (cadless/config.py, .env.example,
and the frontend SettingsPanel). This pins the two backend sources to the
canonical ``anthropic`` so editing one without the other fails CI. The frontend
half is guarded by frontend/src/panels/SettingsPanel.test.tsx.
"""

from __future__ import annotations

from pathlib import Path

from cadless.config import Settings

_ENV_EXAMPLE = Path(__file__).resolve().parents[1] / ".env.example"


def _env_example_provider() -> str:
    for line in _ENV_EXAMPLE.read_text().splitlines():
        if line.startswith("CADLESS_LLM_PROVIDER="):
            return line.split("=", 1)[1].split("#")[0].strip()
    raise AssertionError("CADLESS_LLM_PROVIDER not found in .env.example")


def _code_default_provider() -> str:
    return Settings.model_fields["llm_provider"].default


def test_code_default_provider_is_anthropic():
    assert _code_default_provider() == "anthropic"


def test_env_example_matches_code_default_provider():
    assert _env_example_provider() == _code_default_provider()
