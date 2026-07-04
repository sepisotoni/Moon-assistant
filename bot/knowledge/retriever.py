from __future__ import annotations

from sqlalchemy import select

from bot.database.models import KnowledgeEntry, KnowledgeReviewStatus
from bot.database.session import get_session


class KnowledgeRetriever:
    """Simple keyword-based retrieval over the knowledge_entries table.

    This is intentionally lightweight (ILIKE matching) so the skeleton has no
    external embedding/vector-store dependency. Swap this implementation for a
    pgvector-backed similarity search if richer retrieval is needed later.

    Phase 2/3: only APPROVED entries that have not been superseded by a newer approved
    correction are eligible for retrieval, so a pending/rejected correction can never silently
    change what the AI tells members until a staff member approves it via !ai-knowledge.
    """

    async def search(self, guild_id: int, query: str, limit: int = 5) -> list[KnowledgeEntry]:
        like = f"%{query}%"
        async with get_session() as session:
            stmt = (
                select(KnowledgeEntry)
                .where(
                    KnowledgeEntry.guild_id == guild_id,
                    KnowledgeEntry.content.ilike(like),
                    KnowledgeEntry.review_status == KnowledgeReviewStatus.APPROVED,
                    KnowledgeEntry.superseded_by_id.is_(None),
                )
                .order_by(KnowledgeEntry.created_at.desc())
                .limit(limit)
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())
