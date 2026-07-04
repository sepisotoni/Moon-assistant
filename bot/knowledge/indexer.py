from __future__ import annotations

import discord
from sqlalchemy import delete, select

from bot.database.models import KnowledgeEntry
from bot.database.session import get_session
from bot.knowledge.constants import KNOWLEDGE_CHANNEL_NAMES


def is_knowledge_channel(channel: discord.abc.GuildChannel | discord.Thread) -> bool:
    """True if this channel's name matches one of the configured knowledge channels."""
    name = getattr(channel, "name", "") or ""
    return name.lower() in KNOWLEDGE_CHANNEL_NAMES


class KnowledgeIndexer:
    """Keeps the knowledge_entries table in sync with messages in knowledge channels."""

    async def index_message(self, message: discord.Message) -> None:
        if not message.guild or not is_knowledge_channel(message.channel):
            return
        if not message.content and not message.attachments:
            return

        async with get_session() as session:
            session.add(
                KnowledgeEntry(
                    guild_id=message.guild.id,
                    channel_id=message.channel.id,
                    channel_name=getattr(message.channel, "name", "unknown"),
                    discord_message_id=message.id,
                    author_id=message.author.id,
                    author_name=str(message.author),
                    content=message.content or "",
                )
            )

    async def update_message(self, message: discord.Message) -> None:
        async with get_session() as session:
            stmt = select(KnowledgeEntry).where(KnowledgeEntry.discord_message_id == message.id)
            result = await session.execute(stmt)
            entry = result.scalar_one_or_none()
            if entry is not None:
                entry.content = message.content or ""

    async def remove_message(self, discord_message_id: int) -> None:
        async with get_session() as session:
            await session.execute(
                delete(KnowledgeEntry).where(KnowledgeEntry.discord_message_id == discord_message_id)
            )
