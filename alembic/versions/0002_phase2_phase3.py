"""phase 2 and 3 schema

Revision ID: 0002_phase2_phase3
Revises: 0001_initial
Create Date: 2026-01-02 00:00:00

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_phase2_phase3"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

knowledge_review_status = sa.Enum("approved", "pending", "rejected", name="knowledgereviewstatus")
constitution_tier = sa.Enum("1", "2", "3", "4", "5", name="constitutiontier")
memory_scope = sa.Enum("short_term", "server", "operational", name="memoryscope")
report_status = sa.Enum("pending", "auto_resolved", "escalated", "dismissed", name="reportstatus")
recommended_action = sa.Enum("none", "warn", "delete_message", "timeout", "escalate", name="recommendedaction")
whitelist_status = sa.Enum("pending", "approved", "denied", name="whiteliststatus")


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # Extend existing knowledge_entries with versioning + review columns  #
    # ------------------------------------------------------------------ #
    knowledge_review_status.create(op.get_bind(), checkfirst=True)

    op.add_column("knowledge_entries", sa.Column("confidence_score", sa.Float(), nullable=False, server_default="1.0"))
    op.add_column("knowledge_entries", sa.Column(
        "review_status", knowledge_review_status, nullable=False, server_default="approved"
    ))
    op.add_column("knowledge_entries", sa.Column("reviewed_by", sa.BigInteger(), nullable=True))
    op.add_column("knowledge_entries", sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("knowledge_entries", sa.Column("corrects_entry_id", sa.Integer(), nullable=True))
    op.add_column("knowledge_entries", sa.Column("superseded_by_id", sa.Integer(), nullable=True))

    op.create_foreign_key(
        "fk_knowledge_entries_corrects_entry_id",
        "knowledge_entries", "knowledge_entries",
        ["corrects_entry_id"], ["id"], ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_knowledge_entries_superseded_by_id",
        "knowledge_entries", "knowledge_entries",
        ["superseded_by_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index("ix_knowledge_entries_review_status", "knowledge_entries", ["review_status"])

    # ------------------------------------------------------------------ #
    # knowledge_versions (append-only edit history)                       #
    # ------------------------------------------------------------------ #
    op.create_table(
        "knowledge_versions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("knowledge_entry_id", sa.Integer(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("edited_by", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["knowledge_entry_id"], ["knowledge_entries.id"], ondelete="CASCADE",
                                name="fk_knowledge_versions_knowledge_entry_id"),
        sa.PrimaryKeyConstraint("id", name="pk_knowledge_versions"),
    )
    op.create_index("ix_knowledge_versions_knowledge_entry_id", "knowledge_versions", ["knowledge_entry_id"])

    # ------------------------------------------------------------------ #
    # AI constitution                                                      #
    # ------------------------------------------------------------------ #
    constitution_tier.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "constitution_rules",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tier", constitution_tier, nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("rule_text", sa.Text(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("is_seed_rule", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_constitution_rules"),
    )
    op.create_index("ix_constitution_rules_tier", "constitution_rules", ["tier"])
    op.create_index("ix_constitution_rules_guild_id", "constitution_rules", ["guild_id"])

    # ------------------------------------------------------------------ #
    # AI decision log                                                      #
    # ------------------------------------------------------------------ #
    op.create_table(
        "ai_decision_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=True),
        sa.Column("task_type", sa.String(50), nullable=False),
        sa.Column("requested_by", sa.BigInteger(), nullable=True),
        sa.Column("input_summary", sa.Text(), nullable=False),
        sa.Column("output_summary", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("evidence_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("retrieval_summary", sa.Text(), nullable=True),
        sa.Column("primary_provider", sa.String(50), nullable=True),
        sa.Column("primary_model", sa.String(100), nullable=True),
        sa.Column("secondary_provider", sa.String(50), nullable=True),
        sa.Column("secondary_model", sa.String(100), nullable=True),
        sa.Column("dual_review_agreement", sa.Boolean(), nullable=True),
        sa.Column("escalated", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_ai_decision_logs"),
    )
    op.create_index("ix_ai_decision_logs_guild_id", "ai_decision_logs", ["guild_id"])
    op.create_index("ix_ai_decision_logs_task_type", "ai_decision_logs", ["task_type"])

    # ------------------------------------------------------------------ #
    # Memory system                                                        #
    # ------------------------------------------------------------------ #
    memory_scope.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "memory_entries",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("scope", memory_scope, nullable=False),
        sa.Column("key", sa.String(300), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("hit_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_memory_entries"),
        sa.UniqueConstraint("guild_id", "scope", "key", name="uq_memory_guild_scope_key"),
    )
    op.create_index("ix_memory_entries_guild_id", "memory_entries", ["guild_id"])
    op.create_index("ix_memory_entries_scope", "memory_entries", ["scope"])

    # ------------------------------------------------------------------ #
    # Model registry + health + route overrides + guild settings          #
    # ------------------------------------------------------------------ #
    op.create_table(
        "ai_model_configs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("model_name", sa.String(150), nullable=False),
        sa.Column("task_types", sa.String(300), nullable=False, server_default="*"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("is_free", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_ai_model_configs"),
        sa.UniqueConstraint("provider", "model_name", name="uq_ai_model_configs_provider_model"),
    )
    op.create_table(
        "ai_model_health",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("model_config_id", sa.Integer(), nullable=False, unique=True),
        sa.Column("success_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failure_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_latency_ms", sa.Integer(), nullable=True),
        sa.Column("is_healthy", sa.Boolean(), nullable=False, server_default="true"),
        sa.PrimaryKeyConstraint("id", name="pk_ai_model_health"),
    )
    op.create_index("ix_ai_model_health_model_config_id", "ai_model_health", ["model_config_id"])
    op.create_table(
        "task_route_overrides",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("task_type", sa.String(50), nullable=False),
        sa.Column("model_config_id", sa.Integer(), nullable=False),
        sa.Column("set_by", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_task_route_overrides"),
        sa.UniqueConstraint("guild_id", "task_type", name="uq_task_route_overrides_guild_task"),
    )
    op.create_index("ix_task_route_overrides_guild_id", "task_route_overrides", ["guild_id"])
    op.create_table(
        "guild_settings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("key", sa.String(150), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_guild_settings"),
        sa.UniqueConstraint("guild_id", "key", name="uq_guild_settings_guild_key"),
    )
    op.create_index("ix_guild_settings_guild_id", "guild_settings", ["guild_id"])

    # ------------------------------------------------------------------ #
    # Moderation intelligence                                              #
    # ------------------------------------------------------------------ #
    report_status.create(op.get_bind(), checkfirst=True)
    recommended_action.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "moderation_reports",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("reported_user_id", sa.BigInteger(), nullable=False),
        sa.Column("reporter_id", sa.BigInteger(), nullable=True),
        sa.Column("channel_id", sa.BigInteger(), nullable=True),
        sa.Column("reported_message_id", sa.BigInteger(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("source", sa.String(50), nullable=False, server_default="user"),
        sa.Column("status", report_status, nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_moderation_reports"),
    )
    op.create_index("ix_moderation_reports_guild_id", "moderation_reports", ["guild_id"])
    op.create_index("ix_moderation_reports_reported_user_id", "moderation_reports", ["reported_user_id"])
    op.create_index("ix_moderation_reports_status", "moderation_reports", ["status"])
    op.create_table(
        "moderation_analyses",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("report_id", sa.Integer(), nullable=False),
        sa.Column("risk_score", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("recommended_action", recommended_action, nullable=False),
        sa.Column("evidence_summary", sa.Text(), nullable=False),
        sa.Column("primary_model", sa.String(150), nullable=False),
        sa.Column("secondary_model", sa.String(150), nullable=True),
        sa.Column("agreement", sa.Boolean(), nullable=True),
        sa.Column("action_taken", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_moderation_analyses"),
    )
    op.create_index("ix_moderation_analyses_report_id", "moderation_analyses", ["report_id"])
    op.create_table(
        "staff_escalations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("related_report_id", sa.Integer(), nullable=True),
        sa.Column("related_investigation_id", sa.Integer(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("resolved", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("resolved_by", sa.BigInteger(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_staff_escalations"),
    )
    op.create_index("ix_staff_escalations_guild_id", "staff_escalations", ["guild_id"])

    # ------------------------------------------------------------------ #
    # Investigations                                                       #
    # ------------------------------------------------------------------ #
    op.create_table(
        "investigations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("requested_by", sa.BigInteger(), nullable=False),
        sa.Column("target_user_id", sa.BigInteger(), nullable=True),
        sa.Column("intent", sa.String(50), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_investigations"),
    )
    op.create_index("ix_investigations_guild_id", "investigations", ["guild_id"])
    op.create_table(
        "investigation_findings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("investigation_id", sa.Integer(), nullable=False),
        sa.Column("tool_key", sa.String(100), nullable=False),
        sa.Column("finding_text", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_investigation_findings"),
    )
    op.create_index("ix_investigation_findings_investigation_id", "investigation_findings", ["investigation_id"])

    # ------------------------------------------------------------------ #
    # Reference data (whitelist, linked accounts, known issues)           #
    # ------------------------------------------------------------------ #
    whitelist_status.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "known_issues",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("is_resolved", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_known_issues"),
    )
    op.create_index("ix_known_issues_guild_id", "known_issues", ["guild_id"])
    op.create_index("ix_known_issues_is_resolved", "known_issues", ["is_resolved"])
    op.create_table(
        "linked_accounts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("discord_user_id", sa.BigInteger(), nullable=False),
        sa.Column("ingame_username", sa.String(100), nullable=False),
        sa.Column("linked_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_linked_accounts"),
        sa.UniqueConstraint("guild_id", "discord_user_id", name="uq_linked_accounts_guild_user"),
    )
    op.create_index("ix_linked_accounts_guild_id", "linked_accounts", ["guild_id"])
    op.create_index("ix_linked_accounts_discord_user_id", "linked_accounts", ["discord_user_id"])
    op.create_table(
        "whitelist_entries",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("ingame_username", sa.String(100), nullable=False),
        sa.Column("discord_user_id", sa.BigInteger(), nullable=True),
        sa.Column("status", whitelist_status, nullable=False, server_default="pending"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_whitelist_entries"),
        sa.UniqueConstraint("guild_id", "ingame_username", name="uq_whitelist_guild_username"),
    )
    op.create_index("ix_whitelist_entries_guild_id", "whitelist_entries", ["guild_id"])


def downgrade() -> None:
    for table in (
        "whitelist_entries", "linked_accounts", "known_issues",
        "investigation_findings", "investigations",
        "staff_escalations", "moderation_analyses", "moderation_reports",
        "guild_settings", "task_route_overrides", "ai_model_health", "ai_model_configs",
        "memory_entries", "ai_decision_logs", "constitution_rules", "knowledge_versions",
    ):
        op.drop_table(table)

    for col in ("confidence_score", "review_status", "reviewed_by", "reviewed_at",
                "corrects_entry_id", "superseded_by_id"):
        op.drop_column("knowledge_entries", col)

    for enum in (whitelist_status, recommended_action, report_status, memory_scope,
                 constitution_tier, knowledge_review_status):
        enum.drop(op.get_bind(), checkfirst=True)
