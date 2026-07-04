from __future__ import annotations

import logging

import discord
from sqlalchemy import select

from bot.database.models import KnowledgeEntry
from bot.database.session import get_session
from bot.repositories.knowledge_repository import KnowledgeReviewRepository

logger = logging.getLogger(__name__)


class KnowledgeLearningService:
    """Phase 2/3 additions on top of the Phase 1 KnowledgeIndexer: versioning + review workflow.

    Phase 1's KnowledgeIndexer (bot/knowledge/indexer.py) is left untouched and still owns the
    "auto-index every message posted directly in a knowledge channel" behavior -- those are
    auto-approved, since a human already chose to post them there. This service adds three
    things on top:
      1. version history capture on edit (`on_knowledge_message_edited`)
      2. a correction workflow for staff/AI-suggested fixes that need approval before they
         affect retrieval (`propose_correction`, `approve_correction`, `reject_correction`)
      3. confidence-score bookkeeping
    """

    def __init__(self) -> None:
        self._review_repo = KnowledgeReviewRepository()

    async def on_knowledge_message_edited(self, message: discord.Message) -> None:
        """Snapshot the pre-edit content as a new version before the live row is overwritten.

        Call this BEFORE bot.knowledge.indexer.KnowledgeIndexer.update_message() runs, from the
        same on_message_edit handler, so the version captured is the *old* content.
        """
        async with get_session() as session:
            stmt = select(KnowledgeEntry).where(KnowledgeEntry.discord_message_id == message.id)
            result = await session.execute(stmt)
            entry = result.scalar_one_or_none()
        if entry is None:
            return
        try:
            await self._review_repo.snapshot_version(entry, edited_by=message.author.id)
        except Exception:
            logger.exception("Failed to snapshot knowledge version for message %s", message.id)

    async def propose_correction(
        self,
        *,
        guild_id: int,
        channel_id: int,
        channel_name: str,
        original_entry_id: int,
        discord_message_id: int,
        author_id: int,
        author_name: str,
        content: str,
    ) -> KnowledgeEntry:
        """Submit a correction (from a staff member or an AI suggestion) for review.

        The correction does NOT affect retrieval until approved via !ai-knowledge approve.
        """
        return await self._review_repo.create_correction(
            guild_id=guild_id,
            channel_id=channel_id,
            channel_name=channel_name,
            original_entry_id=original_entry_id,
            discord_message_id=discord_message_id,
            author_id=author_id,
            author_name=author_name,
            content=content,
        )

    async def list_pending(self, guild_id: int) -> list[KnowledgeEntry]:
        return await self._review_repo.list_pending(guild_id)

    async def approve(self, entry_id: int, *, reviewed_by: int) -> KnowledgeEntry | None:
        return await self._review_repo.approve(entry_id, reviewed_by=reviewed_by)

    async def reject(self, entry_id: int, *, reviewed_by: int) -> KnowledgeEntry | None:
        return await self._review_repo.reject(entry_id, reviewed_by=reviewed_by)

    async def history(self, knowledge_entry_id: int):
        return await self._review_repo.history(knowledge_entry_id)
