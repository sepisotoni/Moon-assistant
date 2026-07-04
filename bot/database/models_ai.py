from __future__ import annotations

import datetime as dt
import enum

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    Float,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from bot.database.base import Base


# ---------------------------------------------------------------------------
# AI Constitution
# ---------------------------------------------------------------------------
class ConstitutionTier(int, enum.Enum):
    """Lower number == higher priority. A higher-priority tier always overrides a lower one."""

    PLATFORM_SAFETY = 1
    CORE_BOT = 2
    SERVER = 3
    ROLE = 4
    TASK = 5


class ConstitutionRule(Base):
    """A single rule in the hierarchical AI constitution, stored in the DB for runtime updates.

    NOTE: rules here are *advisory/instructive* — they steer the model's behavior via the system
    prompt. Anything truly safety-critical (no kicks, no bans, no permission changes, 60-minute
    timeout ceiling) is *also* enforced as a hard code-level invariant in
    bot/moderation/action_guard.py, which does not depend on this table being correctly populated.
    """

    __tablename__ = "constitution_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tier: Mapped[ConstitutionTier] = mapped_column(Enum(ConstitutionTier), index=True)
    # NULL guild_id == applies to every guild (used for PLATFORM_SAFETY / CORE_BOT tiers).
    guild_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(200))
    rule_text: Mapped[str] = mapped_column(Text)
    priority: Mapped[int] = mapped_column(Integer, default=100)  # ordering *within* a tier
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # Seeded, foundational rules (see migration data seed) are flagged so the UI/commands can
    # warn before disabling them, even though they technically can be edited at runtime.
    is_seed_rule: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), onupdate=func.now())


# ---------------------------------------------------------------------------
# Confidence system / decision audit trail
# ---------------------------------------------------------------------------
class AIDecisionLog(Base):
    """Every AI-produced decision (answer, recommendation, classification) with its confidence.

    This is the backbone of the "confidence system" requirement: every AI decision is expected
    to be traceable back to a row here with its confidence score, evidence count, and whether it
    triggered an escalation.
    """

    __tablename__ = "ai_decision_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guild_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    task_type: Mapped[str] = mapped_column(String(50), index=True)  # e.g. "support", "moderation_review"
    requested_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    input_summary: Mapped[str] = mapped_column(Text)
    output_summary: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float)
    evidence_count: Mapped[int] = mapped_column(Integer, default=0)
    retrieval_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    primary_provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    primary_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    secondary_provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    secondary_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    dual_review_agreement: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    escalated: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ---------------------------------------------------------------------------
# Memory system
# ---------------------------------------------------------------------------
class MemoryScope(str, enum.Enum):
    SHORT_TERM = "short_term"  # conversational, expires quickly (minutes)
    SERVER = "server"  # facts about the server, rarely expires
    OPERATIONAL = "operational"  # recurring issues/resolutions staff care about, rarely expires


class MemoryEntry(Base):
    """Key/value memory store, scoped per guild.

    `key` is an application-defined namespaced string, e.g.:
      - short_term:  "conversation:<channel_id>:<user_id>"
      - server:      "fact:server_ip"
      - operational: "recurring_question:join_issue"
    """

    __tablename__ = "memory_entries"
    __table_args__ = (UniqueConstraint("guild_id", "scope", "key", name="uq_memory_guild_scope_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, index=True)
    scope: Mapped[MemoryScope] = mapped_column(Enum(MemoryScope), index=True)
    key: Mapped[str] = mapped_column(String(300))
    value: Mapped[str] = mapped_column(Text)  # free-form text or JSON-encoded payload
    hit_count: Mapped[int] = mapped_column(Integer, default=1)  # how many times this has recurred
    expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), onupdate=func.now())


# ---------------------------------------------------------------------------
# Model routing & health
# ---------------------------------------------------------------------------
class AIModelConfig(Base):
    """A candidate (provider, model) pair the router may select, configurable without code changes."""

    __tablename__ = "ai_model_configs"
    __table_args__ = (UniqueConstraint("provider", "model_name", name="uq_ai_model_configs_provider_model"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(50))  # "openrouter" | "gemini"
    model_name: Mapped[str] = mapped_column(String(150))
    # Comma-separated task types this model is eligible for, or "*" for any task.
    task_types: Mapped[str] = mapped_column(String(300), default="*")
    priority: Mapped[int] = mapped_column(Integer, default=100)  # lower == tried first
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_free: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AIModelHealth(Base):
    """Passive health tracking for an AIModelConfig, updated from real call outcomes."""

    __tablename__ = "ai_model_health"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    model_config_id: Mapped[int] = mapped_column(Integer, index=True, unique=True)
    success_count: Mapped[int] = mapped_column(Integer, default=0)
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    last_success_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_failure_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_healthy: Mapped[bool] = mapped_column(Boolean, default=True)


class TaskRouteOverride(Base):
    """An Owner/Founder-set manual pin of which model to use for a task ("/aimodel set").

    Removing this row (or "/aimodel auto") restores automatic priority/health-based routing.
    """

    __tablename__ = "task_route_overrides"
    __table_args__ = (UniqueConstraint("guild_id", "task_type", name="uq_task_route_overrides_guild_task"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, index=True)
    task_type: Mapped[str] = mapped_column(String(50))
    model_config_id: Mapped[int] = mapped_column(Integer)
    set_by: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ---------------------------------------------------------------------------
# Generic per-guild settings (AI kill switch, maintenance mode flag, etc.)
# ---------------------------------------------------------------------------
class GuildSetting(Base):
    """Generic per-guild key/value settings (e.g. ai_enabled, maintenance_mode)."""

    __tablename__ = "guild_settings"
    __table_args__ = (UniqueConstraint("guild_id", "key", name="uq_guild_settings_guild_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, index=True)
    key: Mapped[str] = mapped_column(String(150))
    value: Mapped[str] = mapped_column(Text)  # "true"/"false" or JSON-encoded
    updated_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    updated_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), onupdate=func.now())
