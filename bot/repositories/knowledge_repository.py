from __future__ import annotations

from sqlalchemy import select

from bot.database.models import KnowledgeEntry, KnowledgeReviewStatus
from bot.database.models_knowledge_ext import KnowledgeVersion
from bot.database.session import get_session


class KnowledgeReviewRepository:
    """Data access for knowledge versioning and the correction/approval workflow.

    Sits alongside (does not replace) Phase 1's KnowledgeIndexer/KnowledgeRetriever, which still
    own the "auto-index every message in a knowledge channel" behavior.
    """

    async def snapshot_version(self, entry: KnowledgeEntry, *, edited_by: int | None) -> KnowledgeVersion:
        """Archive the entry's *current* content as a new version row before it changes."""
        async with get_session() as session:
            stmt = select(KnowledgeVersion).where(KnowledgeVersion.knowledge_entry_id == entry.id)
            result = await session.execute(stmt)
            existing = list(result.scalars().all())
            next_version = (max((v.version_number for v in existing), default=0)) + 1
            version = KnowledgeVersion(
                knowledge_entry_id=entry.id,
                version_number=next_version,
                content=entry.content,
                edited_by=edited_by,
            )
            session.add(version)
            await session.flush()
            await session.refresh(version)
            return version

    async def history(self, knowledge_entry_id: int) -> list[KnowledgeVersion]:
        async with get_session() as session:
            stmt = (
                select(KnowledgeVersion)
                .where(KnowledgeVersion.knowledge_entry_id == knowledge_entry_id)
                .order_by(KnowledgeVersion.version_number.desc())
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def create_correction(
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
        """Submit a proposed correction as a new PENDING entry linked to the entry it would replace."""
        async with get_session() as session:
            correction = KnowledgeEntry(
                guild_id=guild_id,
                channel_id=channel_id,
                channel_name=channel_name,
                discord_message_id=discord_message_id,
                author_id=author_id,
                author_name=author_name,
                content=content,
                confidence_score=0.5,
                review_status=KnowledgeReviewStatus.PENDING,
                corrects_entry_id=original_entry_id,
            )
            session.add(correction)
            await session.flush()
            await session.refresh(correction)
            return correction

    async def list_pending(self, guild_id: int) -> list[KnowledgeEntry]:
        async with get_session() as session:
            stmt = select(KnowledgeEntry).where(
                KnowledgeEntry.guild_id == guild_id,
                KnowledgeEntry.review_status == KnowledgeReviewStatus.PENDING,
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def approve(self, entry_id: int, *, reviewed_by: int) -> KnowledgeEntry | None:
        import datetime as dt

        async with get_session() as session:
            entry = await session.get(KnowledgeEntry, entry_id)
            if entry is None:
                return None
            entry.review_status = KnowledgeReviewStatus.APPROVED
            entry.confidence_score = 1.0
            entry.reviewed_by = reviewed_by
            entry.reviewed_at = dt.datetime.now(dt.timezone.utc)

            if entry.corrects_entry_id is not None:
                superseded = await session.get(KnowledgeEntry, entry.corrects_entry_id)
                if superseded is not None:
                    superseded.superseded_by_id = entry.id

            await session.flush()
            await session.refresh(entry)
            return entry

    async def reject(self, entry_id: int, *, reviewed_by: int) -> KnowledgeEntry | None:
        import datetime as dt

        async with get_session() as session:
            entry = await session.get(KnowledgeEntry, entry_id)
            if entry is None:
                return None
            entry.review_status = KnowledgeReviewStatus.REJECTED
            entry.reviewed_by = reviewed_by
            entry.reviewed_at = dt.datetime.now(dt.timezone.utc)
            await session.flush()
            await session.refresh(entry)
            return entry
