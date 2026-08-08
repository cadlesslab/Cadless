"""Runtime user-settings layer tests.

Covers the JSON-backed settings store that the Settings UI writes through:
env > saved > default precedence, no-restart apply (singleton mutation +
os.environ), key masking in status(), and the openai model-repoint guard.
All offline — no real API calls, no live markers.
"""

from __future__ import annotations

import json

import pytest

from cadless import user_settings
from cadless.config import settings

_MANAGED_ATTRS = (
    "llm_provider",
    "orchestrator_model",
    "codegen_model",
    "aws_region",
    # Tuning knobs share the singleton, so they need the same save/restore.
    "rag_top_k",
    "rag_similarity_floor",
    "rag_success_weight",
    "rag_require_tag_overlap",
    "bedrock_temperature",
    "forge_temperature",
    "vlm_model_slug",
    "bedrock_model_slug",
    "bedrock_fast_model_slug",
    # The singleton is process-wide, so a gated knob set here leaks into every
    # later test in the run — including other files — unless it is restored.
    "vlm_critique_enabled",
    "forge_enabled",
    "forge_candidate_count",
    "forge_min_n",
    "forge_max_n",
    "repair_max_attempts",
    "bedrock_max_tokens",
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
    # Named so a leaked export is restored, and so the "never exported" test
    # cannot pass merely because some earlier test left the variable unset.
    "CADLESS_RAG_TOP_K",
    "CADLESS_RAG_REQUIRE_TAG_OVERLAP",
    "CADLESS_BEDROCK_TEMPERATURE",
)


@pytest.fixture(autouse=True)
def isolate(monkeypatch, tmp_path):
    """Point settings.json at a temp dir; restore singleton attrs + managed env.

    ``save()`` mutates the module ``settings`` singleton and ``os.environ``
    directly (not via monkeypatch), so snapshot and restore them by hand.
    ``_ENV_AT_START`` defaults to empty so tests start from "nothing pinned by
    env" and opt individual vars in.
    """
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(user_settings, "_ENV_AT_START", frozenset())
    saved_attrs = {a: getattr(settings, a) for a in _MANAGED_ATTRS}
    import os

    saved_env = {k: os.environ.get(k) for k in _MANAGED_ENV}
    yield
    for a, v in saved_attrs.items():
        setattr(settings, a, v)
    for k, v in saved_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def test_save_roundtrip_applies_and_masks(tmp_path):
    import os

    status = user_settings.save({"provider": "anthropic", "anthropic_api_key": "sk-secret-123"})

    # Applied with no restart: singleton mutated, key written to os.environ.
    assert settings.llm_provider == "anthropic"
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-secret-123"
    # Persisted to <data_dir>/settings.json.
    saved = json.loads((tmp_path / "settings.json").read_text())
    assert saved["provider"] == "anthropic"
    # status() masks the key — value never leaks into the response.
    assert status["secrets"]["anthropic_api_key"] == {"set": True, "source": "saved"}
    assert "sk-secret-123" not in json.dumps(status)


def test_env_var_wins_over_saved_file():
    import os

    os.environ["ANTHROPIC_API_KEY"] = "env-key"
    # Pretend this env var was present at process start.
    import cadless.user_settings as us

    us._ENV_AT_START = frozenset({"anthropic_api_key"})

    status = user_settings.save({"anthropic_api_key": "file-key"})

    # Env wins: the launch-time value is not overwritten.
    assert os.environ["ANTHROPIC_API_KEY"] == "env-key"
    assert status["secrets"]["anthropic_api_key"] == {"set": True, "source": "env"}
    # ...but the file still records it for a future env-less launch.
    assert user_settings.load()["anthropic_api_key"] == "file-key"


def test_openai_provider_requires_model_repoint():
    # Defaults are Claude slugs (opus-4-6 / sonnet-4-6) — switching to openai
    # without repointing the models must fail with actionable guidance.
    with pytest.raises(ValueError, match="CADLESS_ORCHESTRATOR_MODEL"):
        user_settings.save({"provider": "openai"})
    # A failed save must not persist.
    assert user_settings.load() == {}
    # Repointed to OpenAI ids: accepted.
    status = user_settings.save(
        {
            "provider": "openai",
            "orchestrator_model": "gpt-4o",
            "codegen_model": "gpt-4o",
        }
    )
    assert status["provider"] == "openai"
    assert settings.orchestrator_model == "gpt-4o"


def test_unknown_provider_rejected():
    with pytest.raises(ValueError, match="unknown provider"):
        user_settings.save({"provider": "bogus"})


def test_apply_startup_reapplies_saved_file():
    user_settings.save({"provider": "anthropic"})
    # Simulate a fresh process sitting at some other provider before startup.
    settings.llm_provider = "openai"
    user_settings.apply_startup()
    assert settings.llm_provider == "anthropic"


def test_tuning_knob_falsy_value_round_trips():
    """A knob set to a falsy value is a set value, not an absent one.

    The plain fields are all strings, so nothing before this could be saved as
    ``False``/``0``/``0.0``. Provenance decided "saved" by truthiness, which
    reports a deliberately-disabled knob as though it had never been touched.
    """
    status = user_settings.save({"rag_require_tag_overlap": False})

    assert settings.rag_require_tag_overlap is False
    assert user_settings.load()["rag_require_tag_overlap"] is False
    assert user_settings.source("rag_require_tag_overlap") == "saved"
    assert status["rag_require_tag_overlap"] is False
    assert status["rag_require_tag_overlap_source"] == "saved"


def test_tuning_knob_zero_is_saved_not_default():
    """Same rule for a numeric zero — 0.0 is a temperature, not a missing one."""
    user_settings.save({"bedrock_temperature": 0.0})

    assert settings.bedrock_temperature == 0.0
    assert user_settings.source("bedrock_temperature") == "saved"


def test_tuning_knobs_are_not_exported_to_environment():
    """Knobs apply to the singleton only — they must not enter ``os.environ``.

    ``cadless/worker.py`` runs generated code in a subprocess spawned without an
    explicit ``env``, so everything exported here is inherited by the process
    that executes untrusted geometry code. A key has to be exported for the
    vendor SDK to see it; a tuning value buys nothing by being there.
    """
    import os

    user_settings.save({"rag_top_k": 7, "rag_require_tag_overlap": True})

    assert settings.rag_top_k == 7
    assert settings.rag_require_tag_overlap is True
    assert "CADLESS_RAG_TOP_K" not in os.environ
    assert "CADLESS_RAG_REQUIRE_TAG_OVERLAP" not in os.environ


def test_a_file_only_secret_is_saved_without_being_exported(monkeypatch):
    """`_FILE_ONLY_SECRETS` is the promise that a credential a client is handed
    directly stays out of the environment the code-execution subprocess inherits.

    That set is empty in this build — every secret it ships is read by a vendor
    SDK, which is what buys those the export. Exercised against a stand-in
    rather than left unexercised: the guard in `_apply` is what a build adding a
    credential of its own relies on, and an empty set makes the promise
    unfalsifiable, not true.
    """
    import os

    monkeypatch.setattr(user_settings, "_FILE_ONLY_SECRETS", frozenset({"anthropic_api_key"}))
    os.environ.pop("ANTHROPIC_API_KEY", None)

    user_settings.save({"anthropic_api_key": "sk-held-in-the-file"})

    # Saved, and readable back by the code that has to present it...
    assert user_settings.secret("anthropic_api_key") == "sk-held-in-the-file"
    # ...and never handed to the process that runs generated code.
    assert "ANTHROPIC_API_KEY" not in os.environ


@pytest.mark.parametrize(
    ("patch", "match"),
    [
        # Compared against a cosine similarity, so outside [0, 1] the floor
        # silently turns retrieval into all-or-nothing instead of filtering.
        ({"rag_similarity_floor": 1.5}, "rag_similarity_floor"),
        ({"rag_similarity_floor": -0.1}, "rag_similarity_floor"),
        # blended_score is `similarity + weight * success_score` with
        # success_score in [0, 1): at weight >= 1 the success signal can outrank
        # similarity outright, which is not the documented "breaks near-ties".
        ({"rag_success_weight": 1.5}, "rag_success_weight"),
        ({"rag_success_weight": -0.2}, "rag_success_weight"),
        # rag.py treats <= 0 as the no-retrieval path, so negatives are not an
        # error there — but they are a typo here, and the candidate pool is 4x.
        ({"rag_top_k": -1}, "rag_top_k"),
        ({"rag_top_k": 500}, "rag_top_k"),
        # The union of what the vendor APIs accept.
        ({"bedrock_temperature": 2.5}, "bedrock_temperature"),
        ({"forge_temperature": -0.1}, "forge_temperature"),
    ],
)
def test_out_of_range_knob_rejected(patch, match):
    with pytest.raises(ValueError, match=match):
        user_settings.save(patch)
    # Validation runs before anything is written: nothing persisted, nothing applied.
    assert user_settings.load() == {}


def test_boundary_values_accepted():
    """The bounds are inclusive — 0 retrieval and a 0 floor are real settings."""
    user_settings.save({"rag_top_k": 0, "rag_similarity_floor": 0.0, "rag_success_weight": 1.0})
    assert settings.rag_top_k == 0
    assert settings.rag_similarity_floor == 0.0
    assert settings.rag_success_weight == 1.0


def test_cost_multiplying_knob_refused_without_the_launch_gate():
    """Tier B is refused, not silently dropped — the caller learns it did nothing.

    The settings endpoint is unauthenticated, so a knob that multiplies per-turn
    spend is a cost-exhaustion vector. The gate is a launch-environment decision
    precisely because a request must not be able to grant itself the privilege.
    """
    with pytest.raises(ValueError, match="CADLESS_SETTINGS_ADVANCED"):
        user_settings.save({"forge_enabled": True})
    assert user_settings.load() == {}
    assert settings.forge_enabled is False


def test_a_hosted_build_refuses_a_credential(monkeypatch):
    """On a build that hosts more than one person, every caller here is a stranger.

    The endpoint has no authentication of its own, so what a request would be
    writing is the *installation's* credential — and the next generation anybody
    ran would spend it. The refusal names the launch variable for the same reason
    the Tier B one does: an operator reading it should be able to tell a posture
    from a bug.
    """
    monkeypatch.setattr(settings, "require_identity", True)

    with pytest.raises(ValueError, match="CADLESS_REQUIRE_IDENTITY"):
        user_settings.save({"anthropic_api_key": "sk-xyz"})

    assert user_settings.load() == {}
    assert user_settings.secret("anthropic_api_key") is None


def test_a_hosted_build_refuses_a_harmless_field_too(monkeypatch):
    """The whole write, not only the credentials — and this is the case that says so.

    ``settings.json`` is one file for the installation, so a visitor changing the
    model changes it for everybody, and a gate that let this through would close
    the headline hole while leaving a real one. Refusing the credential fields
    alone was considered and rejected for exactly this.
    """
    monkeypatch.setattr(settings, "require_identity", True)

    with pytest.raises(ValueError, match="CADLESS_REQUIRE_IDENTITY"):
        user_settings.save({"codegen_model": "sonnet-4-6"})

    assert user_settings.load() == {}


@pytest.mark.parametrize(
    "patch",
    [
        {"provider": "bogus"},
        {"provider": "openai"},
        {"forge_enabled": True},
        {"rag_top_k": 999},
        {"vlm_model_slug": "not-a-slug"},
    ],
    ids=["unknown-provider", "model-repoint", "tier-b", "out-of-range", "unknown-slug"],
)
def test_the_hosted_refusal_wins_over_every_other_complaint(monkeypatch, patch):
    """Ordering, which the code claimed and nothing held.

    Moving the refusal after any of the other checks leaves the whole suite
    green — measured, twice — while a hosted build starts answering an
    unauthenticated caller with the installation's configured model, the entire
    model catalogue, whether the advanced gate is on, and every accepted range.
    Each of those is something the refusal exists to keep unlearnable, so the
    property under test is not "it refuses" but "it refuses *first*".
    """
    monkeypatch.setattr(settings, "require_identity", True)

    with pytest.raises(ValueError) as caught:
        user_settings.save(patch)

    message = str(caught.value)
    assert "CADLESS_REQUIRE_IDENTITY" in message
    for disclosure in (
        "choose one of",
        "CADLESS_SETTINGS_ADVANCED",
        "outside the accepted range",
        "Bedrock/Claude",
    ):
        assert disclosure not in message


def test_a_hosted_build_refuses_to_forget_a_credential_too(monkeypatch):
    """`clear()` is a write, and the reason for putting the guard in this module
    rather than at the route has to hold for it as well.

    No route reaches it today, but the engine loads add-on routers through an
    entry-point seam — an add-on offering "forget my key" would otherwise remove
    the installation's credential for everybody who shares the build.
    """
    user_settings.save({"anthropic_api_key": "sk-xyz"})
    monkeypatch.setattr(settings, "require_identity", True)

    with pytest.raises(ValueError, match="CADLESS_REQUIRE_IDENTITY"):
        user_settings.clear("anthropic_api_key")

    assert user_settings.secret("anthropic_api_key") == "sk-xyz"


def test_an_empty_patch_is_not_the_thing_being_refused(monkeypatch):
    """A patch that asks for nothing changes nothing, hosted or not.

    Worth pinning rather than leaving to chance: a refusal keyed on "hosted"
    alone would turn a no-op into an error, and the endpoint's own model builds
    an empty dict whenever every field was omitted.
    """
    monkeypatch.setattr(settings, "require_identity", True)

    assert user_settings.save({}) == user_settings.status()


def test_the_gate_is_not_reachable_through_the_thing_it_gates(monkeypatch):
    """The flag cannot be turned off by the endpoint it protects.

    Structural rather than incidental: ``require_identity`` is in none of the
    field maps, so there is no patch that reaches it and no ordering of saves
    that opens the gate. Asserted here because the whole refusal rests on it.
    """
    assert "require_identity" not in user_settings._ALL_FIELDS
    assert "require_identity" not in user_settings._SETTINGS_ATTR

    # And the structure is what holds it, not the refusal: `save` maps only the
    # fields it knows, so even reached directly — no endpoint, no gate — the flag
    # is untouched by a patch that names it.
    monkeypatch.setattr(settings, "require_identity", False)
    user_settings.save({"require_identity": True})
    assert settings.require_identity is False


def test_cost_multiplying_knob_accepted_when_the_gate_is_set(monkeypatch):
    monkeypatch.setattr(user_settings, "_ADVANCED_ENABLED", True)

    user_settings.save({"forge_enabled": True, "forge_candidate_count": 4})

    assert settings.forge_enabled is True
    assert settings.forge_candidate_count == 4


def test_gated_knob_still_range_checked(monkeypatch):
    monkeypatch.setattr(user_settings, "_ADVANCED_ENABLED", True)
    with pytest.raises(ValueError, match="forge_candidate_count"):
        user_settings.save({"forge_candidate_count": 99})


def test_env_pinned_tuning_knob_wins_over_saved_file():
    """Precedence is unchanged for knobs: a launch-time env var still wins."""
    import cadless.user_settings as us

    settings.rag_top_k = 9  # what the launch environment resolved to
    us._ENV_AT_START = frozenset({"rag_top_k"})

    status = user_settings.save({"rag_top_k": 2})

    assert settings.rag_top_k == 9  # not overwritten
    assert status["rag_top_k_source"] == "env"
    # ...but the file still records it for a future env-less launch.
    assert user_settings.load()["rag_top_k"] == 2


@pytest.mark.parametrize(
    "field", ["vlm_model_slug", "bedrock_model_slug", "bedrock_fast_model_slug"]
)
def test_unknown_model_slug_rejected(field):
    """A slug is resolved by a bare PROFILES[...] lookup, so it must be checked here.

    Accepted, it would persist, reapply on every restart, and only surface as a
    KeyError on the next generation — with no API path back, since clear()
    covers only secrets.
    """
    with pytest.raises(ValueError, match=field):
        user_settings.save({field: "totally-bogus"})
    assert user_settings.load() == {}


def test_known_model_slug_accepted():
    from cadless.model_profiles import PROFILES

    slug = next(iter(PROFILES))
    user_settings.save({"vlm_model_slug": slug})
    assert settings.vlm_model_slug == slug


def test_temperature_above_one_rejected():
    """The bound is the intersection across providers, not the union.

    One field feeds all three adapters and two of them send it to Claude, whose
    Messages API rejects above 1.0 — and the default provider is anthropic.
    """
    with pytest.raises(ValueError, match="bedrock_temperature"):
        user_settings.save({"bedrock_temperature": 1.5})
    user_settings.save({"bedrock_temperature": 1.0})  # the boundary is usable
    assert settings.bedrock_temperature == 1.0


def test_gate_allows_lowering_spend_but_not_raising():
    """Refusing "turn it off" would leave no way back once it had been turned on."""
    settings.forge_enabled = True
    user_settings.save({"forge_enabled": False})  # gate closed, but this spends less
    assert settings.forge_enabled is False

    with pytest.raises(ValueError, match="CADLESS_SETTINGS_ADVANCED"):
        user_settings.save({"forge_enabled": True})


def test_gate_allows_reducing_a_numeric_budget():
    settings.forge_candidate_count = 5
    user_settings.save({"forge_candidate_count": 2})
    assert settings.forge_candidate_count == 2
    with pytest.raises(ValueError, match="CADLESS_SETTINGS_ADVANCED"):
        user_settings.save({"forge_candidate_count": 9})


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, False),
        ("", False),
        ("0", False),
        ("false", False),
        ("FALSE", False),
        ("no", False),
        ("off", False),
        ("1", True),
        ("true", True),
        ("yes", True),
    ],
)
def test_gate_env_flag_parsing(monkeypatch, value, expected):
    """`=false` must not open a security gate: bare truthiness would open it."""
    if value is None:
        monkeypatch.delenv("CADLESS_SETTINGS_ADVANCED", raising=False)
    else:
        monkeypatch.setenv("CADLESS_SETTINGS_ADVANCED", value)
    assert user_settings._env_flag("CADLESS_SETTINGS_ADVANCED") is expected


def test_closing_the_gate_withdraws_an_already_saved_value(monkeypatch):
    """A gate that cannot be withdrawn is not a boundary.

    The file outlives the launch that allowed the write, and apply_startup
    replays it on every boot.
    """
    monkeypatch.setattr(user_settings, "_ADVANCED_ENABLED", True)
    user_settings.save({"forge_enabled": True})
    assert settings.forge_enabled is True

    monkeypatch.setattr(user_settings, "_ADVANCED_ENABLED", False)
    settings.forge_enabled = False  # a fresh process starts at the code default
    user_settings.apply_startup()
    assert settings.forge_enabled is False  # the saved raise is not replayed


def test_int_knob_rejects_a_fraction():
    with pytest.raises(ValueError, match="rag_top_k"):
        user_settings.save({"rag_top_k": 3.7})


def test_bool_knob_rejects_a_non_bool():
    with pytest.raises(ValueError, match="rag_require_tag_overlap"):
        user_settings.save({"rag_require_tag_overlap": "yes"})


def test_numeric_knob_rejects_a_string():
    with pytest.raises(ValueError, match="rag_similarity_floor"):
        user_settings.save({"rag_similarity_floor": "high"})


def test_no_tuning_knob_reaches_the_environment(monkeypatch):
    """Asserted over every knob, not a sample.

    worker.py spawns the code-execution subprocess with no explicit env, so the
    child inherits whatever is exported. Checking two of them would let a later
    addition slip through.
    """
    import os

    monkeypatch.setattr(user_settings, "_ADVANCED_ENABLED", True)
    values = {
        "rag_top_k": 4,
        "rag_similarity_floor": 0.4,
        "rag_success_weight": 0.3,
        "rag_require_tag_overlap": True,
        "bedrock_temperature": 0.5,
        "forge_temperature": 0.6,
        "forge_enabled": True,
        "vlm_critique_enabled": True,
        "forge_candidate_count": 4,
        "repair_max_attempts": 4,
        "bedrock_max_tokens": 3000,
    }
    before = {env: os.environ.get(env) for env in user_settings._TUNING_FIELDS.values()}
    user_settings.save(values)

    for field, value in values.items():
        assert getattr(settings, field) == value, field
    for field, env in user_settings._TUNING_FIELDS.items():
        assert os.environ.get(env) == before[env], f"{field} leaked into {env}"
