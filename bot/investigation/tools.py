from __future__ import annotations

import datetime as dt

from bot.database.models import ModerationAction
from bot.database.session import get_session
from bot.investigation.base import InvestigationContext, InvestigationTool, ToolFinding
from bot.knowledge.constants import KNOWLEDGE_CHANNEL_NAMES
from bot.knowledge.retriever import KnowledgeRetriever
from bot.repositories.moderation_intel_repository import InvestigationRepository
from sqlalchemy import select


class WhitelistStatusTool(InvestigationTool):
    key = "whitelist_status"

    def __init__(self) -> None:
        self._repo = InvestigationRepository()

    async def run(self, context: InvestigationContext) -> ToolFinding:
        if context.target_user_id is None:
            return ToolFinding(self.key, "No target user was specified to check whitelist status for.", 0.2)
        entry = await self._repo.whitelist_entry(context.guild.id, discord_user_id=context.target_user_id)
        if entry is None:
            return ToolFinding(self.key, "No whitelist record found for this user.", 0.5)
        return ToolFinding(
            self.key,
            f"Whitelist status for '{entry.ingame_username}': {entry.status.value}.",
            0.9,
        )


class KnownIssuesTool(InvestigationTool):
    """Surfaces open known issues (e.g. maintenance, outages) staff have logged."""

    key = "known_issues"

    def __init__(self) -> None:
        self._repo = InvestigationRepository()

    async def run(self, context: InvestigationContext) -> ToolFinding:
        issues = await self._repo.known_issues(context.guild.id, only_open=True)
        if not issues:
            return ToolFinding(self.key, "No open known issues are logged for this server right now.", 0.7)
        lines = "; ".join(f"{i.title}: {i.description}" for i in issues[:5])
        return ToolFinding(self.key, f"Open known issues: {lines}", 0.85)


class PunishmentHistoryTool(InvestigationTool):
    """Looks up this bot's own moderation_actions history for the target user."""

    key = "punishment_history"

    async def run(self, context: InvestigationContext) -> ToolFinding:
        if context.target_user_id is None:
            return ToolFinding(self.key, "No target user was specified to check punishment history for.", 0.2)

        async with get_session() as session:
            stmt = (
                select(ModerationAction)
                .where(
                    ModerationAction.guild_id == context.guild.id,
                    ModerationAction.user_id == context.target_user_id,
                )
                .order_by(ModerationAction.created_at.desc())
                .limit(5)
            )
            result = await session.execute(stmt)
            rows = list(result.scalars().all())

        if not rows:
            return ToolFinding(self.key, "No moderation actions on record for this user (via this bot).", 0.6)

        lines = "; ".join(
            f"{r.action_type.value} on {r.created_at:%Y-%m-%d} ({r.reason or 'no reason given'})" for r in rows
        )
        return ToolFinding(self.key, f"Recent moderation history: {lines}", 0.9)


class LinkedAccountTool(InvestigationTool):
    key = "linked_account"

    def __init__(self) -> None:
        self._repo = InvestigationRepository()

    async def run(self, context: InvestigationContext) -> ToolFinding:
        if context.target_user_id is None:
            return ToolFinding(self.key, "No target user was specified to check account linking for.", 0.2)
        link = await self._repo.linked_account(context.guild.id, context.target_user_id)
        if link is None:
            return ToolFinding(self.key, "This Discord account is not linked to an in-game account.", 0.7)
        return ToolFinding(self.key, f"Linked in-game username: {link.ingame_username}.", 0.9)


class RecentAnnouncementsTool(InvestigationTool):
    """Surfaces the most recent knowledge-channel posts as potential context (e.g. #ai-news)."""

    key = "recent_announcements"

    def __init__(self) -> None:
        self._retriever = KnowledgeRetriever()

    async def run(self, context: InvestigationContext) -> ToolFinding:
        # Empty-string ILIKE ('%%') matches everything; we rely on ordering + limit for recency.
        hits = await self._retriever.search(context.guild.id, "", limit=3)
        announcement_hits = [h for h in hits if h.channel_name in KNOWLEDGE_CHANNEL_NAMES]
        if not announcement_hits:
            return ToolFinding(self.key, "No recent announcements found.", 0.4)
        lines = "; ".join(f"[{h.channel_name}] {h.content[:150]}" for h in announcement_hits)
        return ToolFinding(self.key, f"Recent announcements: {lines}", 0.7)


class MaintenanceStatusTool(InvestigationTool):
    """A known-issue-backed maintenance check (no external server-status API in this skeleton).

    Wire a real server-status/RCON/API integration here when one is available; for now this
    looks for an open known issue whose title mentions maintenance/outage/downtime.
    """

    key = "maintenance_status"

    def __init__(self) -> None:
        self._repo = InvestigationRepository()

    async def run(self, context: InvestigationContext) -> ToolFinding:
        issues = await self._repo.known_issues(context.guild.id, only_open=True)
        maintenance_terms = ("maintenance", "outage", "downtime", "restart")
        hits = [i for i in issues if any(term in i.title.lower() or term in i.description.lower() for term in maintenance_terms)]
        if not hits:
            return ToolFinding(self.key, "No active maintenance or outage is currently logged.", 0.6)
        lines = "; ".join(f"{i.title}: {i.description}" for i in hits)
        return ToolFinding(self.key, f"Active maintenance/outage: {lines}", 0.9)
