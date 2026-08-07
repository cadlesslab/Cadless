"""Runtime settings endpoints.

GET /settings returns a masked snapshot (provider, model overrides, and whether
each API key is set — never a key value). POST /settings validates, persists to
``runtime-db/settings.json``, and applies the change without a restart. The
endpoint is unauthenticated, so the tool must bind 127.0.0.1 (see backend.main /
the compose proxy). Delegates all logic to :mod:`cadless.user_settings`.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from cadless import user_settings

router = APIRouter(prefix="/settings", tags=["settings"])


class SettingsUpdate(BaseModel):
    """Partial settings patch — every field optional; omitted/blank ones are ignored.

    Unknown fields are refused rather than ignored. Operator-only configuration —
    where the execution worker lives, the sandbox's resource limits, the width
    of an embedding — is deliberately absent from this model, and a silent drop
    would leave a caller believing a setting took effect. Refusing says so, and
    keeps the omission from reading as an oversight.
    """

    model_config = ConfigDict(extra="forbid")

    provider: str | None = None
    orchestrator_model: str | None = None
    codegen_model: str | None = None
    aws_region: str | None = None
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    aws_access_key_id: str | None = None
    aws_secret_access_key: str | None = None
    aws_session_token: str | None = None
    # Engine tuning knobs, typed rather than str so a JSON number arrives as a
    # number. The default stays None so `exclude_none` can tell "not sent" from
    # "sent as 0/false" — for a knob those are different instructions, where for
    # a string field they never were. Range checks live in `user_settings`,
    # which `save()` enforces for Python callers too.
    rag_top_k: int | None = None
    rag_similarity_floor: float | None = None
    rag_success_weight: float | None = None
    rag_require_tag_overlap: bool | None = None
    bedrock_temperature: float | None = None
    forge_temperature: float | None = None
    vlm_model_slug: str | None = None
    bedrock_model_slug: str | None = None
    bedrock_fast_model_slug: str | None = None
    # Cost-multiplying knobs are declared here on purpose, even though they are
    # refused unless the launch gate is set. Leaving them off the model would
    # make `extra="forbid"` answer 422 — the same reply operator-only fields get
    # — and a caller could not tell "this is never settable" from "switch the
    # gate on". Declared, the refusal comes from `user_settings.validate` with a
    # 400 that names the variable to set.
    vlm_critique_enabled: bool | None = None
    forge_enabled: bool | None = None
    forge_candidate_count: int | None = None
    forge_min_n: int | None = None
    forge_max_n: int | None = None
    repair_max_attempts: int | None = None
    bedrock_max_tokens: int | None = None


@router.get("")
async def get_settings() -> dict:
    return user_settings.status()


@router.post("")
async def update_settings(body: SettingsUpdate) -> dict:
    try:
        return user_settings.save(body.model_dump(exclude_none=True))
    except ValueError as exc:
        # Unusable provider/model combination (e.g. openai with Claude-slug models).
        raise HTTPException(status_code=400, detail=str(exc)) from exc
