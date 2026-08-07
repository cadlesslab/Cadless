"""Application settings (pydantic-settings, env prefix ``CADLESS_``).

Mirrors the-Engine-v2 config pattern: model selection is by slug,
resolved to a Bedrock ID via :mod:`cadless.model_profiles` at access time so
a bad slug fails fast.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from cadless.model_profiles import resolve_model_id


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CADLESS_", env_file=".env", extra="ignore")

    # Storage — DB + artifact blobs live under data_dir.
    data_dir: Path = Path("runtime-db")

    @property
    def db_path(self) -> Path:
        # One-time migration from the pre-rename product name: adopt an
        # existing vulcan.db so upgraded deployments keep their data. Never
        # overwrites a cadless.db that already exists.
        new = self.data_dir / "cadless.db"
        legacy = self.data_dir / "vulcan.db"
        if legacy.exists() and not new.exists():
            try:
                legacy.rename(new)
            except FileNotFoundError:
                pass  # a concurrently starting process won the rename
        return new

    @property
    def artifacts_dir(self) -> Path:
        return self.data_dir / "artifacts"

    # Catalog content (house + mechanical catalogs, eval prompts) lives OUTSIDE the
    # repo so it survives Docker image rebuilds via a bind mount, and is
    # the location the authoring pipeline writes generated parts/houses into.
    # Override with CADLESS_CATALOG_ROOT; in docker it is bind-mounted to the same
    # path so the default resolves identically on host and in the container.
    catalog_root: Path = Path("./catalog")

    def domain_catalog_dir(self, domain: str) -> Path:
        """Content root for a registered catalog domain (#46).

        Registry-driven: the domain's entry declares its dirname under
        ``catalog_root``, so adding a domain never touches this module.
        Raises ``ValueError`` for unregistered domains.
        """
        # Imported lazily so config stays import-light and cycle-free.
        from cadless.catalog.domains import get_domain

        return self.catalog_root / get_domain(domain).content_dir

    @property
    def house_catalog_dir(self) -> Path:
        return self.domain_catalog_dir("house")

    @property
    def mech_catalog_dir(self) -> Path:
        return self.domain_catalog_dir("mechanical")

    # Whether this build is hosting more than one person, and therefore must not
    # start without something able to say who is asking.
    #
    # Off is the local tool: no resolver, one implicit user, nothing to
    # configure. On is a deployment declaring that identity is not optional, and
    # it makes a missing or broken resolver a refusal to boot rather than a
    # silent fall back to that one implicit user — which would leave everyone
    # reading everyone else's work while the app answered normally.
    #
    # It is a launch decision rather than a runtime one, and deliberately not
    # settable through the settings endpoint: an unauthenticated caller must not
    # be able to switch identity off.
    require_identity: bool = False

    # CORS — comma-separated origins via CADLESS_CORS_ALLOW_ORIGINS.
    cors_allow_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]

    # Mount prefix behind the platform Caddy (e.g. /apps/cadless/api). Only affects
    # OpenAPI/docs link generation; the bundled Caddy strips the prefix before
    # requests reach the app. Empty for bare local dev.
    root_path: str = ""

    # Pluggable LLM seam — provider selection + per-role model slugs.
    # `llm_provider` picks the ChatProvider built by cadless.llm.registry;
    # the role slugs resolve through model_profiles like the bedrock slugs below.
    llm_provider: str = "anthropic"  # bedrock | anthropic | openai | fake
    orchestrator_model: str = (
        "opus-4-6"  # Opus-tier planning/orchestration (override via CADLESS_ORCHESTRATOR_MODEL)
    )
    codegen_model: str = "sonnet-4-6"  # build123d code generation

    # Embeddings — vendor-neutral embed() seam. Default backend is
    # Bedrock Titan Text Embeddings V2, reusing the Bedrock region/creds below.
    # The model id is surfaced here so the backend is swappable. Titan V2 supports
    # 256/512/1024 output dimensions; 1024 is the model default.
    embed_model_id: str = "amazon.titan-embed-text-v2:0"
    embed_dimensions: int = 1024
    # OpenAI embeddings backend: used when llm_provider=openai. The text-embedding-3
    # family accepts a dimensions override, so embed_dimensions above applies to it
    # too (keeps the KB vector store dimension-stable). Anthropic has no embeddings
    # API — RAG/KB embed calls skip cleanly there.
    openai_embed_model: str = "text-embedding-3-small"

    # Dynamic RAG retrieval into the codegen prompt. At generation we
    # embed the request the same way B3 embedded entries, retrieve top-k known-good
    # KB entries (cosine similarity + feature-tag filter) behind a similarity floor,
    # and inject them as grounding. Purely additive: an empty KB (or all candidates
    # below the floor) degrades to the no-retrieval prompt.
    rag_top_k: int = 3  # how many grounding examples to inject
    rag_similarity_floor: float = 0.55  # min cosine sim for a candidate to qualify
    rag_success_weight: float = 0.2  # blend weight for the provenance success signal
    rag_require_tag_overlap: bool = False  # require >=1 feature-tag overlap to retain

    # Transcript compaction / session hygiene. Long chat sessions grow
    # the neutral `history` replayed into each turn unboundedly; once it exceeds the
    # threshold we fold the OLDER turns into a single rolling synopsis and keep only
    # the most recent N messages verbatim, so agent context stays bounded regardless
    # of session length. The durable code source of truth stays the script_versions
    # chain — compaction only rewrites the conversational transcript fed to the model.
    # Purely additive: a session at/below the threshold replays today's full history.
    transcript_compact_threshold: int = 40  # message count above which to compact
    transcript_keep_recent: int = 12  # most-recent messages kept verbatim

    # AWS / Bedrock
    aws_region: str = "us-east-1"
    bedrock_model_slug: str = "sonnet-4-6"  # primary codegen
    bedrock_fast_model_slug: str = "haiku-4-5"  # cheap checks / fast path
    bedrock_max_tokens: int = 2000
    bedrock_temperature: float = 0.0
    bedrock_max_retries: int = 4

    # Execution worker limits
    exec_timeout_secs: float = 30.0  # wall-clock; also drives the child CPU rlimit
    # Offline catalog bake/authoring timeout (#34). Re-authored house steps run
    # 20-200s+ of pure OCCT booleans (cumulative boolean ladders); the 30s limit
    # above is tuned for *live user codegen* and must not gate offline runs
    # over committed step code, which take far longer. Read by the private
    # authoring pipeline, which consumes this module as a library.
    bake_exec_timeout_secs: float = 300.0

    # Remote execution worker. When set (e.g. http://worker:9000), the
    # api delegates code execution to the isolated worker service over the internal
    # network instead of spawning a local subprocess. Empty = run locally.
    worker_url: str = ""

    # Repair loop
    repair_max_attempts: int = 3

    # Forge mode best-of-N fan-out (C1). For a flagged fresh generation
    # we fan out N independent generate runs IN PARALLEL at a higher temperature
    # for diversity, then (C2) judge/select among them. C1 ships only the parallel
    # primitive + this temperature plumbing; the on/off toggle and live wiring are
    # gated behind C4, so these defaults are inert until then.
    forge_candidate_count: int = 3  # N candidates to fan out when forge is on
    forge_temperature: float = 0.8  # higher temp for candidate diversity

    # Forge mode toggle + budget-scaling (C4). Forge is default-OFF and
    # opt-in: a turn must opt in (the per-turn `forge` flag on the chat request) AND
    # `forge_enabled` must be on for the race to run (both-true gate). This global
    # switch is the kill-switch — flipping it off disables forge for every turn
    # regardless of opt-in, so the expensive N×tokens + N×OCCT path is never on by
    # accident.
    forge_enabled: bool = False  # global kill-switch; both-true gate with per-turn opt-in
    # N is BUDGET-SCALED, not a hard constant: N = clamp(forge_budget //
    # forge_candidate_cost, forge_min_n, forge_max_n). Budget/cost are in the same
    # abstract unit (a relative "spend"); their ratio is how many candidates the
    # budget buys, then clamped to a sane window. Defaults buy a 3-way race.
    forge_budget: int = 6  # total spend allotted to one forge turn
    forge_candidate_cost: int = 2  # spend per candidate (tokens+OCCT, abstract unit)
    forge_min_n: int = 2  # floor: a race needs >=2 samples to be a race
    forge_max_n: int = 5  # ceiling: cap the cost blast-radius of one turn

    def forge_scaled_n(self) -> int:
        """Budget-scaled candidate count for an active forge turn (pure helper).

        ``N = clamp(forge_budget // forge_candidate_cost, forge_min_n, forge_max_n)``.
        The budget divided by the per-candidate cost is how many candidates the
        spend buys; clamping keeps N within ``[forge_min_n, forge_max_n]`` so a huge
        budget can't blow up cost and a tiny one still races. A non-positive cost
        degrades to ``forge_max_n`` rather than dividing by zero. Deterministic and
        side-effect free so it is trivially unit-testable.
        """
        raw = (
            self.forge_max_n
            if self.forge_candidate_cost <= 0
            else (self.forge_budget // self.forge_candidate_cost)
        )
        return max(self.forge_min_n, min(self.forge_max_n, raw))

    # Conversational agent loop — hard caps that bound one user turn so
    # a misbehaving model can never run the tool loop unboundedly.
    agent_max_tool_iters: int = 6  # max tool round-trips per user turn
    agent_token_budget: int = 200_000  # cumulative tokens/turn before forced stop
    agent_time_budget_secs: float = 120.0  # wall-clock/turn before forced stop
    # Per-CALL output cap for the orchestrator. Distinct from the codegen
    # bedrock_max_tokens (2000, fine for short build123d scripts): an orchestrator
    # turn streams extended thinking PLUS text PLUS a tool call, and 2000 truncates
    # that — a cut-off tool call arrives with empty input and stop_reason=max_tokens,
    # so the action silently never runs. Give the agent ample headroom.
    agent_max_tokens: int = 8192
    # Convergence/cycle awareness: after this many failed build tool
    # calls at the SAME repair stage within one turn, the agent appends guidance
    # steering the model to ask_clarification instead of burning more budget on the
    # same failure. Layered above the identical-tool-call debounce.
    agent_same_stage_escalation: int = 2

    # Optional VLM render-critique repair signal — OFF by default
    vlm_critique_enabled: bool = False
    vlm_model_slug: str = "sonnet-4-6"  # vision-capable

    @property
    def vlm_model_id(self) -> str:
        return resolve_model_id(self.vlm_model_slug)

    # Tessellation
    gltf_deflection: float = 0.1

    @property
    def bedrock_model_id(self) -> str:
        return resolve_model_id(self.bedrock_model_slug)

    @property
    def bedrock_fast_model_id(self) -> str:
        return resolve_model_id(self.bedrock_fast_model_slug)


settings = Settings()
