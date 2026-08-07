"""User-editable runtime settings, JSON-backed.

A small settings layer the Settings UI writes through so a user can pick an LLM
provider, enter API keys, and set model overrides at runtime — persisted to
``<data_dir>/settings.json`` and applied without a restart.

Precedence is **environment variable > saved settings.json > code default**: a
value explicitly present in the environment at process start is never overwritten
by the saved file (protects operators who configure via env/.env). API keys are
not part of :class:`cadless.config.Settings` — the vendor SDKs read them
straight from ``os.environ`` — so applying a key means writing ``os.environ``;
the SDK clients are lazy and ``build_provider()`` runs per request, so the next
request picks the new key up with no cache to invalidate. Non-secret ``CADLESS_*``
fields are applied by mutating the shared ``settings`` singleton in place
(pydantic v2 models are mutable), which every ``from cadless.config import
settings`` reader sees immediately.

Keys are never returned or logged: :func:`status` reports only whether a key is
set and where it came from, never its value.
"""

from __future__ import annotations

import json
import os
from typing import Any

from cadless.config import settings
from cadless.model_profiles import PROFILES

# Non-secret UI field -> environment variable (CADLESS_*) it corresponds to.
_PLAIN_FIELDS: dict[str, str] = {
    "provider": "CADLESS_LLM_PROVIDER",
    "orchestrator_model": "CADLESS_ORCHESTRATOR_MODEL",
    "codegen_model": "CADLESS_CODEGEN_MODEL",
    "aws_region": "CADLESS_AWS_REGION",
}
# Non-secret UI field -> Settings attribute mutated in place for no-restart apply.
_SETTINGS_ATTR: dict[str, str] = {
    "provider": "llm_provider",
    "orchestrator_model": "orchestrator_model",
    "codegen_model": "codegen_model",
    "aws_region": "aws_region",
}
# Engine tuning knobs. Same no-restart apply as the plain fields, but **never
# exported to the environment**: `cadless/worker.py` runs generated code in a
# subprocess spawned without an explicit env, so everything exported here is
# inherited by the process that executes untrusted geometry. A vendor key earns
# that export because the SDK has no other way to receive it; a tuning value
# earns nothing by it, and the child has no use for one.
#
# Each still names a variable, because an operator may pin one at launch and env
# must keep winning. The name is derived rather than spelled out: `Settings` is a
# pydantic ``BaseSettings`` with ``env_prefix="CADLESS_"``, so this is exactly the
# variable it already reads for that field — writing the names by hand would be a
# second copy of a rule that already exists.
_TUNING_FIELDS: dict[str, str] = {
    field: f"CADLESS_{field.upper()}"
    for field in (
        "rag_top_k",
        "rag_similarity_floor",
        "rag_success_weight",
        "rag_require_tag_overlap",
        "bedrock_temperature",
        "forge_temperature",
        "vlm_model_slug",
        "bedrock_model_slug",
        "bedrock_fast_model_slug",
    )
}
# A knob's UI name is its Settings attribute, so the mapping is the identity.
_SETTINGS_ATTR.update({field: field for field in _TUNING_FIELDS})

# Inclusive bounds, each taken from how the value is consumed rather than taste:
#
# * ``rag_similarity_floor`` is compared against a cosine similarity, so outside
#   [0, 1] it stops filtering and silently becomes all-or-nothing.
# * ``rag_success_weight`` is the ``w`` in ``similarity + w * success_score``
#   with ``success_score`` in [0, 1) (:func:`cadless.rag.blended_score`). At
#   ``w >= 1`` the success signal can outrank similarity outright, which is not
#   the "similarity-first, breaks near-ties" ranking that module documents.
# * The temperature bound is the **intersection** of what the vendor APIs accept,
#   not their union: one field is read by all three adapters
#   (``providers/{anthropic,bedrock,openai}.py``) and two of them send it to
#   Claude, whose Messages API rejects anything above 1.0 with a 400. The default
#   provider is ``anthropic`` (``config.py:101``), so a union bound would let an
#   unauthenticated caller persist a value that breaks generation outright.
# * ``rag_top_k``'s ceiling is a **typo guard, not an intrinsic limit** — there
#   is no natural maximum, but ``rag.py`` pulls a candidate pool of ``top_k * 4``
#   and every retained example goes into the prompt, so a slipped digit is
#   expensive. Its floor is 0 because ``rag.py`` already treats ``<= 0`` as the
#   no-retrieval path: zero is a setting, and only a negative is a mistake.
_RANGES: dict[str, tuple[float, float]] = {
    "rag_top_k": (0, 50),
    "rag_similarity_floor": (0.0, 1.0),
    "rag_success_weight": (0.0, 1.0),
    "bedrock_temperature": (0.0, 1.0),
    "forge_temperature": (0.0, 1.0),
}
# Knobs whose value must be a whole number. `_RANGES` alone would accept 3.7 for
# a count, and the config layer stores what it is given.
_INT_FIELDS: frozenset[str] = frozenset({"rag_top_k"})
_BOOL_FIELDS: frozenset[str] = frozenset({"rag_require_tag_overlap"})
# Knobs naming a model. `resolve_model_id` is a bare `PROFILES[slug]`, so an
# unknown slug is accepted here and raises KeyError later, on the next
# generation — and `clear()` refuses non-secret fields, so there is no way back
# through the API. `validate` already catches this class at save time for the
# orchestrator/codegen models; these were exposed without the same guard.
_SLUG_FIELDS: frozenset[str] = frozenset(
    {"vlm_model_slug", "bedrock_model_slug", "bedrock_fast_model_slug"}
)

# Tier B: knobs that multiply what one turn spends. The settings endpoint has no
# authentication (see backend/routers/settings.py), so letting an unauthenticated
# caller raise these is a cost-exhaustion vector, not a matter of taste. They are
# settable only when an operator opts in at launch — a deployment boundary rather
# than a UI affordance, because anything the interface hides is still one curl
# away. The gate is read once, here, so a request cannot flip it.
_ADVANCED_GATE = "CADLESS_SETTINGS_ADVANCED"


def _env_flag(name: str) -> bool:
    """Read an environment variable as a boolean.

    Bare truthiness would read ``"false"`` and ``"0"`` as on — the two values an
    operator is most likely to write precisely to record that a gate is off. A
    security boundary that opens when someone writes ``=false`` is worse than no
    boundary, because it is believed to be closed.
    """
    return os.environ.get(name, "").strip().lower() not in ("", "0", "false", "no", "off")


_ADVANCED_ENABLED: bool = _env_flag(_ADVANCED_GATE)
_TIER_B_FIELDS: dict[str, str] = {
    field: f"CADLESS_{field.upper()}"
    for field in (
        "vlm_critique_enabled",
        "forge_enabled",
        "forge_candidate_count",
        "forge_min_n",
        "forge_max_n",
        "repair_max_attempts",
        "bedrock_max_tokens",
    )
}
_TUNING_FIELDS.update(_TIER_B_FIELDS)
_SETTINGS_ATTR.update({field: field for field in _TIER_B_FIELDS})
_BOOL_FIELDS |= {"vlm_critique_enabled", "forge_enabled"}
# Upper bounds here are what the source already calls them: forge_max_n exists to
# "cap the cost blast-radius of one turn", so these are that cap's own cap.
# repair_max_attempts' floor is not invented either — pipeline.py runs
# `max(1, repair_max_attempts)`, so below 1 was already coerced.
_RANGES.update(
    {
        "forge_candidate_count": (1, 10),
        "forge_min_n": (2, 10),  # config: "a race needs >=2 samples to be a race"
        "forge_max_n": (2, 10),
        "repair_max_attempts": (1, 10),
        "bedrock_max_tokens": (1, 64_000),
    }
)
# Secret UI field -> environment variable the vendor SDK reads it from. A
# credential no vendor SDK reads still gets the same treatment: env-over-file
# precedence, and never echoed back.
_SECRET_FIELDS: dict[str, str] = {
    "anthropic_api_key": "ANTHROPIC_API_KEY",
    "openai_api_key": "OPENAI_API_KEY",
    "aws_access_key_id": "AWS_ACCESS_KEY_ID",
    "aws_secret_access_key": "AWS_SECRET_ACCESS_KEY",
    "aws_session_token": "AWS_SESSION_TOKEN",
}
_ALL_FIELDS: dict[str, str] = {**_PLAIN_FIELDS, **_TUNING_FIELDS, **_SECRET_FIELDS}

# Secrets that are read back through :func:`secret` rather than exported. An
# operator may still pin one in the environment — that is why each still names a
# variable — but saving one must not put it there: `cadless/worker.py` spawns the
# code-execution subprocess without an explicit env, so every exported value is
# inherited by the child that runs generated code. The vendor SDKs have no other
# way to receive a key, which is what buys them the export; a client handed its
# credential directly gains nothing from one and belongs here instead. Empty in
# this build, because every credential it ships is read by a vendor SDK.
_FILE_ONLY_SECRETS: frozenset[str] = frozenset()

# Saved state that is neither configuration nor secret: it persists in the same
# file but maps to no environment variable and no Settings attribute, so it is
# stored and reported without being applied to the running process. Empty in
# this build, because nothing it ships keeps state of that shape.
_SAVED_ONLY_FIELDS: tuple[str, ...] = ()

PROVIDERS: tuple[str, ...] = ("bedrock", "anthropic", "openai")

# Managed env vars that were explicitly set at process start. These win over the
# saved file for the life of the process (env > file precedence). Recomputed once
# at import; tests override it to simulate different launch environments.
_ENV_AT_START: frozenset[str] = frozenset(
    field for field, env in _ALL_FIELDS.items() if os.environ.get(env)
)


def _settings_path():
    return settings.data_dir / "settings.json"


def load() -> dict[str, Any]:
    """Return the saved settings dict (``{}`` if absent or unreadable)."""
    try:
        return json.loads(_settings_path().read_text())
    except (FileNotFoundError, ValueError):
        return {}


def _write(data: dict[str, Any]) -> None:
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
    tmp.replace(path)  # atomic on POSIX


def _current_plain() -> dict[str, Any]:
    """The current effective non-secret values, read from the live singleton."""
    return {field: getattr(settings, attr) for field, attr in _SETTINGS_ATTR.items()}


def _effective_after(patch: dict[str, Any]) -> dict[str, Any]:
    eff = _current_plain()
    for field in _PLAIN_FIELDS:
        # Presence, not truthiness — same reason as :func:`_source`. ``save``
        # has already dropped ``None``/``""`` before validation sees the patch.
        if field in patch:
            eff[field] = patch[field]
    return eff


def validate(patch: dict[str, Any]) -> None:
    """Raise ``ValueError`` on an unusable provider/model combination.

    Mirrors the openai adapter's fail-fast (``providers/openai.py`` ``_check_model``,
    same ``PROFILES`` membership rule): when the effective provider is openai the
    orchestrator/codegen models must be OpenAI ids, not the Claude-slug defaults, or
    generation 404s. Surfaced here so the UI shows a readable error at save time.
    """
    provider = patch.get("provider")
    if provider is not None and provider not in PROVIDERS:
        raise ValueError(f"unknown provider {provider!r}; choose one of {', '.join(PROVIDERS)}")
    effective = _effective_after(patch)
    if effective.get("provider") == "openai":
        for field in ("orchestrator_model", "codegen_model"):
            model = effective.get(field)
            if not model or model in PROFILES or str(model).startswith("claude-"):
                raise ValueError(
                    f"{field}={model!r} is a Bedrock/Claude model; when provider=openai, set "
                    f"{_PLAIN_FIELDS[field]} to an OpenAI model id (e.g. 'gpt-4o')"
                )
    _validate_knobs(patch)


def _raises_spend(field: str, value: Any) -> bool:
    """Whether setting ``field`` to ``value`` costs more than it does now.

    Used only by the Tier B gate, which exists to stop an unauthenticated caller
    raising the bill. Anything this cannot compare is treated as a raise, so an
    unexpected type fails closed rather than slipping past the gate.
    """
    current = getattr(settings, _SETTINGS_ATTR.get(field, field), None)
    if isinstance(value, bool) or isinstance(current, bool):
        return bool(value) and not bool(current)
    if isinstance(value, int | float) and isinstance(current, int | float):
        return value > current
    return True


def _validate_knobs(patch: dict[str, Any]) -> None:
    """Reject a knob that is the wrong type or outside its usable range.

    The API layer already types the body, but :func:`save` is callable from
    Python too, so the guard lives with the rule it enforces rather than with
    one of its callers.
    """
    if not _ADVANCED_ENABLED:
        # The gate blocks *raising* spend, not touching the field. Refusing
        # "turn forge off" would leave whoever enabled it with no way back —
        # `clear()` covers only secrets — and stopping a caller from spending
        # less was never the point.
        raised = sorted(
            f for f in _TIER_B_FIELDS.keys() & patch.keys() if _raises_spend(f, patch[f])
        )
        if raised:
            raise ValueError(
                f"{', '.join(raised)} would raise what a single turn spends, so it is "
                f"settable only when {_ADVANCED_GATE} is set in the launch environment"
            )
    for field in _BOOL_FIELDS & patch.keys():
        if not isinstance(patch[field], bool):
            raise ValueError(f"{field}={patch[field]!r} must be true or false")
    for field in _SLUG_FIELDS & patch.keys():
        slug = patch[field]
        if slug not in PROFILES:
            raise ValueError(
                f"{field}={slug!r} is not a known model slug; choose one of "
                f"{', '.join(sorted(PROFILES))}"
            )
    for field in _INT_FIELDS & patch.keys():
        value = patch[field]
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{field}={value!r} must be a whole number")
    for field, (low, high) in _RANGES.items():
        if field not in patch:
            continue
        value = patch[field]
        # `isinstance(True, int)` is True, so a bare numeric check would quietly
        # accept a boolean as 1 and store a type the reader does not expect.
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValueError(f"{field}={value!r} must be a number")
        if not low <= value <= high:
            raise ValueError(f"{field}={value!r} is outside the accepted range [{low}, {high}]")


def _apply(data: dict[str, Any]) -> None:
    """Apply settings to the running process, honouring env-at-start precedence."""
    for field, value in data.items():
        if field not in _ALL_FIELDS or field in _ENV_AT_START:
            continue  # unknown field, or env pinned it at launch — leave it
        if field in _FILE_ONLY_SECRETS:
            continue  # read back through secret(); never exported to children
        if field in _TIER_B_FIELDS and not _ADVANCED_ENABLED and _raises_spend(field, value):
            # Closing the gate has to withdraw what it granted. Validation runs
            # on save, but the file outlives the launch that allowed it, and
            # `apply_startup` replays that file on every boot — so without this,
            # an operator who removes the variable would find the raised value
            # still live. Only a raise is skipped: a saved value that spends less
            # is still applied, or turning a knob off would not stick either.
            continue
        if field not in _TUNING_FIELDS:
            # Knobs reach their readers through the singleton alone — see the
            # _TUNING_FIELDS note on what inherits this environment.
            os.environ[_ALL_FIELDS[field]] = str(value)
        attr = _SETTINGS_ATTR.get(field)
        if attr is not None:
            setattr(settings, attr, value)


def apply_startup() -> None:
    """Apply the saved settings at process start (env precedence honoured)."""
    _apply(load())


def _source(field: str, saved: dict[str, Any]) -> str:
    if field in _ENV_AT_START:
        return "env"
    # Presence, not truthiness: a knob saved as ``False`` or ``0`` is a value
    # somebody chose, and reporting it as "default" would tell the UI the
    # opposite of what the file says. ``save`` never writes ``None``, so this
    # only forgives a hand-edited file.
    if saved.get(field) is not None:
        return "saved"
    return "unset" if field in _SECRET_FIELDS else "default"


def source(field: str) -> str:
    """Where a field's current value comes from: ``env``, ``saved``, or neither.

    The same answer :func:`status` reports, for callers that need one field
    rather than a snapshot — an environment-pinned secret carries none of the
    state saved alongside a value this process stored itself.
    """
    return _source(field, load())


def status() -> dict[str, Any]:
    """A masked snapshot for the UI. Never includes a secret's value."""
    saved = load()
    out: dict[str, Any] = {"providers": list(PROVIDERS)}
    for field in (*_PLAIN_FIELDS, *_TUNING_FIELDS):
        out[field] = getattr(settings, _SETTINGS_ATTR[field])
        out[f"{field}_source"] = _source(field, saved)
    out["secrets"] = {
        field: {
            "set": bool(os.environ.get(env)) or bool(saved.get(field)),
            "source": _source(field, saved),
        }
        for field, env in _SECRET_FIELDS.items()
    }
    for field in _SAVED_ONLY_FIELDS:
        out[field] = saved.get(field)
    return out


def save(patch: dict[str, Any]) -> dict[str, Any]:
    """Validate, persist, then apply a settings patch. Returns the masked status.

    Blank/omitted values are ignored (this endpoint sets values; it does not clear
    them). Validation runs before anything is written, so a rejected patch leaves
    both the file and the running process untouched.
    """
    known = set(_ALL_FIELDS) | set(_SAVED_ONLY_FIELDS)
    patch = {k: v for k, v in patch.items() if k in known and v not in (None, "")}
    validate(patch)
    data = load()
    data.update(patch)
    _write(data)
    _apply(patch)
    return status()


def clear(*fields: str) -> dict[str, Any]:
    """Forget saved values. The counterpart to :func:`save`, which only ever sets.

    Limited to secrets and saved-only state. Clearing a plain configuration field
    would mean restoring a code default into the live singleton, which ``save``
    has no notion of either — those change by saving a new value, not by clearing.

    An environment variable that was set at process start is left in place: we
    did not put it there, and removing it would misreport what the next request
    actually sends. :func:`status` keeps calling such a value ``env``.
    """
    clearable = {f for f in fields if f in _SECRET_FIELDS or f in _SAVED_ONLY_FIELDS}
    unclearable = set(fields) - clearable
    if unclearable:
        # Refused rather than skipped: a caller that believes it signed a user
        # out must not be told that it worked.
        raise ValueError(
            f"cannot clear {', '.join(sorted(unclearable))}; "
            "only credentials and saved state can be cleared"
        )

    data = load()
    for field in clearable:
        data.pop(field, None)
    _write(data)
    for field in (clearable & set(_SECRET_FIELDS)) - _ENV_AT_START:
        os.environ.pop(_SECRET_FIELDS[field], None)
    return status()


def secret(field: str) -> str | None:
    """The effective value of a managed secret, or ``None``.

    Environment first, then the saved file — the same precedence :func:`_apply`
    enforces, kept in one place. :func:`status` deliberately never returns these;
    this is for the code that has to actually present one.
    """
    if field not in _SECRET_FIELDS:
        raise ValueError(f"{field!r} is not a managed secret")
    return os.environ.get(_SECRET_FIELDS[field]) or load().get(field) or None


def has_credentials(provider: str | None = None) -> bool:
    """Whether ``provider`` (or the current one) has usable credentials.

    anthropic/openai need a single API key in the environment. Bedrock resolves AWS
    credentials through the full boto3 chain (env vars, shared config/SSO, instance
    role), so we ask botocore rather than only checking env vars — otherwise a
    working ``~/.aws`` or instance-role setup would be misreported as "no
    credentials" and wrongly blocked.
    """
    provider = provider or settings.llm_provider
    if provider == "anthropic":
        return bool(os.environ.get("ANTHROPIC_API_KEY"))
    if provider == "openai":
        return bool(os.environ.get("OPENAI_API_KEY"))
    if provider == "bedrock":
        try:
            import botocore.session

            return botocore.session.Session().get_credentials() is not None
        except Exception:  # botocore missing or resolution failed -> treat as unset
            return False
    return True  # fake / unknown providers need no credentials


_CREDENTIAL_HINTS: dict[str, str] = {
    "anthropic": (
        "No Anthropic API key is set. Open Settings and add your Anthropic API key to "
        "generate. Browsing and editing the sample catalog works without one."
    ),
    "openai": (
        "No OpenAI API key is set. Open Settings and add your OpenAI API key to "
        "generate. Browsing and editing the sample catalog works without one."
    ),
    "bedrock": (
        "No AWS credentials found for Bedrock. Open Settings to add an AWS access key "
        "and secret, or switch to Anthropic/OpenAI and enter an API key. Browsing and "
        "editing the sample catalog works without one."
    ),
}


def credentials_hint(provider: str | None = None) -> str:
    """An actionable, user-facing message for when ``provider`` has no credentials."""
    provider = provider or settings.llm_provider
    return _CREDENTIAL_HINTS.get(
        provider,
        f"No credentials configured for provider {provider!r}. Open Settings to add them.",
    )
