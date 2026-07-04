from __future__ import annotations

import datetime as dt
import enum

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bot.database.base import Base


class Guild(Base):
    """A Discord server the bot is in."""

    __tablename__ = "guilds"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # Discord guild ID
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    channels: Mapped[list["Channel"]] = relationship(back_populates="guild", cascade="all, delete-orphan")


class Channel(Base):
    """A channel/thread the bot has seen messages in."""

    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # Discord channel/thread ID
    guild_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("guilds.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    type: Mapped[str] = mapped_column(String(50), default="text")
    is_knowledge_channel: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    guild: Mapped["Guild"] = relationship(back_populates="channels")
    messages: Mapped[list["Message"]] = relationship(back_populates="channel", cascade="all, delete-orphan")


class Message(Base):
    """An archived copy of every non-bot message sent in a guild."""

    __tablename__ = "messages"
    __table_args__ = (UniqueConstraint("discord_message_id", name="uq_messages_discord_message_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    discord_message_id: Mapped[int] = mapped_column(BigInteger, index=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("guilds.id", ondelete="CASCADE"), index=True)
    channel_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("channels.id", ondelete="CASCADE"), index=True
    )
    author_id: Mapped[int] = mapped_column(BigInteger, index=True)
    author_name: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text, default="")
    attachment_urls: Mapped[str | None] = mapped_column(Text, nullable=True)  # comma-separated URLs
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    edited_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    channel: Mapped["Channel"] = relationship(back_populates="messages")


class KnowledgeReviewStatus(str, enum.Enum):
    """Phase 2/3: workflow status for knowledge entries.

    Direct posts in a knowledge channel are auto-approved (they were already curated by being
    posted there). AI-suggested or staff-submitted *corrections* start PENDING and only affect
    retrieval once an Owner/Founder approves them via the !ai-knowledge command.
    """

    APPROVED = "approved"
    PENDING = "pending"
    REJECTED = "rejected"


class KnowledgeEntry(Base):
    """Messages from designated knowledge channels (#ai-ip, #ai-faq, #ai-news, #ai-store)."""

    __tablename__ = "knowledge_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, index=True)
    channel_id: Mapped[int] = mapped_column(BigInteger, index=True)
    channel_name: Mapped[str] = mapped_column(String(200))
    discord_message_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    author_id: Mapped[int] = mapped_column(BigInteger)
    author_name: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), onupdate=func.now())

    # --- Phase 2/3: knowledge learning (versioning, confidence, review workflow) ---
    confidence_score: Mapped[float] = mapped_column(Float, default=1.0)
    review_status: Mapped[KnowledgeReviewStatus] = mapped_column(
        Enum(KnowledgeReviewStatus), default=KnowledgeReviewStatus.APPROVED, index=True
    )
    reviewed_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    reviewed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # If this entry is a proposed correction, points at the entry it would replace.
    corrects_entry_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("knowledge_entries.id", ondelete="SET NULL"), nullable=True
    )
    # Set once a newer, approved entry has superseded this one (kept for history, excluded
    # from retrieval).
    superseded_by_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("knowledge_entries.id", ondelete="SET NULL"), nullable=True
    )


class AIRuleScope(str, enum.Enum):
    GLOBAL = "global"
    GUILD = "guild"
    CHANNEL = "channel"


class AIRule(Base):
    """A database-stored rule injected into the AI system prompt."""

    __tablename__ = "ai_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200))
    scope: Mapped[AIRuleScope] = mapped_column(Enum(AIRuleScope), default=AIRuleScope.GUILD)
    guild_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    channel_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    prompt_text: Mapped[str] = mapped_column(Text)
    priority: Mapped[int] = mapped_column(Integer, default=100)  # lower runs first
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[int] = mapped_column(BigInteger)  # Discord user ID
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ModerationActionType(str, enum.Enum):
    WARN = "warn"
    DELETE_MESSAGE = "delete_message"
    TIMEOUT = "timeout"
    UNTIMEOUT = "untimeout"
    KICK = "kick"
    BAN = "ban"


class ModerationAction(Base):
    """An audit record of every moderation action taken, for accountability."""

    __tablename__ = "moderation_actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)  # the moderated member
    moderator_id: Mapped[int] = mapped_column(BigInteger)  # who took the action
    action_type: Mapped[ModerationActionType] = mapped_column(Enum(ModerationActionType))
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BotLog(Base):
    """Application/audit log entries, persisted for dashboards and history."""

    __tablename__ = "bot_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    level: Mapped[str] = mapped_column(String(20))
    source: Mapped[str] = mapped_column(String(200))
    message: Mapped[str] = mapped_column(Text)
    guild_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    meta: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON-encoded extra context
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
