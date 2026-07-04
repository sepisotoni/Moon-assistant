from __future__ import annotations

import datetime as dt

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from bot.database.base import Base


class KnowledgeVersion(Base):
    """Append-only history of a KnowledgeEntry's content over time.

    Whenever a knowledge-channel message is edited, or a correction is approved, the *previous*
    content is archived here before the live KnowledgeEntry row is updated, so staff can always
    see how an answer evolved (and revert if a "correction" turns out to be wrong).
    """

    __tablename__ = "knowledge_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    knowledge_entry_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("knowledge_entries.id", ondelete="CASCADE"), index=True
    )
    version_number: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    edited_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # Discord user ID, if known
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
