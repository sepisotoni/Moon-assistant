"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-01-01 00:00:00

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ai_rule_scope = sa.Enum("global", "guild", "channel", name="airulescope")
moderation_action_type = sa.Enum(
    "warn", "delete_message", "timeout", "untimeout", "kick", "ban", name="moderationactiontype"
)


def upgrade() -> None:
    op.create_table(
        "guilds",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_guilds"),
    )

    op.create_table(
        "channels",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("type", sa.String(length=50), nullable=False),
        sa.Column("is_knowledge_channel", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["guild_id"], ["guilds.id"], name="fk_channels_guild_id_guilds", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_channels"),
    )
    op.create_index("ix_channels_guild_id", "channels", ["guild_id"])

    op.create_table(
        "messages",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("discord_message_id", sa.BigInteger(), nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column("author_id", sa.BigInteger(), nullable=False),
        sa.Column("author_name", sa.String(length=200), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("attachment_urls", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["guild_id"], ["guilds.id"], name="fk_messages_guild_id_guilds", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["channel_id"], ["channels.id"], name="fk_messages_channel_id_channels", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_messages"),
        sa.UniqueConstraint("discord_message_id", name="uq_messages_discord_message_id"),
    )
    op.create_index("ix_messages_discord_message_id", "messages", ["discord_message_id"])
    op.create_index("ix_messages_guild_id", "messages", ["guild_id"])
    op.create_index("ix_messages_channel_id", "messages", ["channel_id"])
    op.create_index("ix_messages_author_id", "messages", ["author_id"])
    op.create_index("ix_messages_is_deleted", "messages", ["is_deleted"])

    op.create_table(
        "knowledge_entries",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column("channel_name", sa.String(length=200), nullable=False),
        sa.Column("discord_message_id", sa.BigInteger(), nullable=False),
        sa.Column("author_id", sa.BigInteger(), nullable=False),
        sa.Column("author_name", sa.String(length=200), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_knowledge_entries"),
        sa.UniqueConstraint("discord_message_id", name="uq_knowledge_entries_discord_message_id"),
    )
    op.create_index("ix_knowledge_entries_guild_id", "knowledge_entries", ["guild_id"])
    op.create_index("ix_knowledge_entries_channel_id", "knowledge_entries", ["channel_id"])
    op.create_index(
        "ix_knowledge_entries_discord_message_id", "knowledge_entries", ["discord_message_id"]
    )

    op.create_table(
        "ai_rules",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("scope", ai_rule_scope, nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=True),
        sa.Column("channel_id", sa.BigInteger(), nullable=True),
        sa.Column("prompt_text", sa.Text(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_ai_rules"),
    )
    op.create_index("ix_ai_rules_guild_id", "ai_rules", ["guild_id"])
    op.create_index("ix_ai_rules_channel_id", "ai_rules", ["channel_id"])

    op.create_table(
        "moderation_actions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("moderator_id", sa.BigInteger(), nullable=False),
        sa.Column("action_type", moderation_action_type, nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("duration_minutes", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_moderation_actions"),
    )
    op.create_index("ix_moderation_actions_guild_id", "moderation_actions", ["guild_id"])
    op.create_index("ix_moderation_actions_user_id", "moderation_actions", ["user_id"])

    op.create_table(
        "bot_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("level", sa.String(length=20), nullable=False),
        sa.Column("source", sa.String(length=200), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=True),
        sa.Column("meta", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_bot_logs"),
    )


def downgrade() -> None:
    op.drop_table("bot_logs")
    op.drop_table("moderation_actions")
    op.drop_table("ai_rules")
    op.drop_table("knowledge_entries")
    op.drop_table("messages")
    op.drop_table("channels")
    op.drop_table("guilds")

    moderation_action_type.drop(op.get_bind(), checkfirst=True)
    ai_rule_scope.drop(op.get_bind(), checkfirst=True)
