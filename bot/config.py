from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All runtime configuration, loaded from environment variables / .env."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Discord ---
    discord_token: str = Field(..., alias="DISCORD_TOKEN")
    command_prefix: str = Field("!", alias="COMMAND_PREFIX")
    # Comma-separated role names that bypass channel-search restrictions and manage AI rules.
    owner_role_names: str = Field("Owner,Founder", alias="OWNER_ROLE_NAMES")

    # --- Database ---
    database_url: str = Field(..., alias="DATABASE_URL")
    db_echo: bool = Field(False, alias="DB_ECHO")

    # --- AI providers ---
    openrouter_api_key: str | None = Field(None, alias="OPENROUTER_API_KEY")
    openrouter_model: str = Field("openai/gpt-4o-mini", alias="OPENROUTER_MODEL")
    openrouter_base_url: str = Field("https://openrouter.ai/api/v1", alias="OPENROUTER_BASE_URL")

    gemini_api_key: str | None = Field(None, alias="GEMINI_API_KEY")
    gemini_model: str = Field("gemini-1.5-flash", alias="GEMINI_MODEL")

    ai_system_prompt: str = Field(
        "You are a helpful, concise Discord community AI assistant.",
        alias="AI_SYSTEM_PROMPT",
    )

    # --- Moderation ---
    # NOTE: this is a *configurable default*, not the safety ceiling. The absolute ceiling is
    # ABSOLUTE_MAX_TIMEOUT_MINUTES in bot/moderation/action_guard.py, which is a Python constant
    # (not env-configurable) so no misconfiguration can ever push timeouts past 60 minutes.
    max_timeout_minutes: int = Field(60, alias="MAX_TIMEOUT_MINUTES")

    # --- Logging & timezone display ---
    # Database always stores UTC. TZ_OFFSET_HOURS only affects how timestamps are *displayed*
    # in log output and Discord embeds (e.g. 2 = UTC+2).
    tz_offset_hours: int = Field(2, alias="TZ_OFFSET_HOURS")
    log_level: str = Field("INFO", alias="LOG_LEVEL")
    log_channel_id: int | None = Field(None, alias="LOG_CHANNEL_ID")
    # Optional channel the bot posts staff escalations (low-confidence / disagreeing dual-review
    # decisions) into, in addition to the bot_logs / staff_escalations DB tables.
    escalation_channel_id: int | None = Field(None, alias="ESCALATION_CHANNEL_ID")

    # --- Phase 2/3: AI Constitution & confidence system ---
    # Below this confidence, the AI must ask a clarifying question or escalate rather than answer.
    confidence_escalation_threshold: float = Field(0.55, alias="CONFIDENCE_ESCALATION_THRESHOLD")
    # Below this confidence, a moderation/investigation recommendation is never auto-applied,
    # even if the dual-review models agree.
    auto_action_confidence_threshold: float = Field(0.75, alias="AUTO_ACTION_CONFIDENCE_THRESHOLD")
    # Whether moderation/investigation tasks require two independent models to agree before
    # an automated action (warn/delete/timeout) is taken. Support Q&A always uses a single model.
    dual_review_enabled: bool = Field(True, alias="DUAL_REVIEW_ENABLED")

    # --- Phase 2/3: model routing ---
    # Comma-separated list of free/low-cost OpenRouter model slugs the router may pick between.
    # The DB-backed ai_model_configs table is the real source of truth at runtime; this is only
    # the seed list used the first time the registry is populated.
    openrouter_candidate_models: str = Field(
        "meta-llama/llama-3.1-8b-instruct:free,google/gemma-2-9b-it:free,mistralai/mistral-7b-instruct:free",
        alias="OPENROUTER_CANDIDATE_MODELS",
    )
    gemini_candidate_models: str = Field("gemini-1.5-flash,gemini-1.5-flash-8b", alias="GEMINI_CANDIDATE_MODELS")
    # Consecutive failures before a model is marked unhealthy and skipped by the router.
    model_unhealthy_after_failures: int = Field(3, alias="MODEL_UNHEALTHY_AFTER_FAILURES")
    # Cooldown before an unhealthy model gets a single retry ("half-open" circuit breaker).
    model_health_cooldown_seconds: int = Field(300, alias="MODEL_HEALTH_COOLDOWN_SECONDS")

    # --- Phase 2/3: memory system ---
    short_term_memory_ttl_seconds: int = Field(1800, alias="SHORT_TERM_MEMORY_TTL_SECONDS")  # 30 min
    # server/operational memory has no expiry by default (None == never expires)

    # --- Phase 2/3: heuristic detectors (in-memory, no DB) ---
    spam_message_threshold: int = Field(6, alias="SPAM_MESSAGE_THRESHOLD")
    spam_window_seconds: int = Field(10, alias="SPAM_WINDOW_SECONDS")
    raid_join_threshold: int = Field(8, alias="RAID_JOIN_THRESHOLD")
    raid_window_seconds: int = Field(30, alias="RAID_WINDOW_SECONDS")
    repeat_offender_warning_count: int = Field(3, alias="REPEAT_OFFENDER_WARNING_COUNT")
    repeat_offender_lookback_hours: int = Field(24, alias="REPEAT_OFFENDER_LOOKBACK_HOURS")

    @property
    def owner_role_name_set(self) -> set[str]:
        return {name.strip().lower() for name in self.owner_role_names.split(",") if name.strip()}

    @property
    def openrouter_candidate_model_list(self) -> list[str]:
        return [m.strip() for m in self.openrouter_candidate_models.split(",") if m.strip()]

    @property
    def gemini_candidate_model_list(self) -> list[str]:
        return [m.strip() for m in self.gemini_candidate_models.split(",") if m.strip()]


@lru_cache
def get_settings() -> Settings:
    """Cached settings singleton. Call get_settings.cache_clear() in tests if needed."""
    return Settings()  # type: ignore[call-arg]
