from __future__ import annotations

import datetime as dt

import discord
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import Channel, Guild, Message
from bot.database.session import get_session


class ArchiveService:
    """Persists every message (and edits/deletions) into Postgres."""

    async def _ensure_guild(self, session: AsyncSession, guild: discord.Guild) -> None:
        db_guild = await session.get(Guild, guild.id)
        if db_guild is None:
            session.add(Guild(id=guild.id, name=guild.name))
        elif db_guild.name != guild.name:
            db_guild.name = guild.name

    async def _ensure_channel(
        self,
        session: AsyncSession,
        channel: discord.abc.GuildChannel | discord.Thread,
        *,
        is_knowledge: bool,
    ) -> None:
        db_channel = await session.get(Channel, channel.id)
        if db_channel is None:
            session.add(
                Channel(
                    id=channel.id,
                    guild_id=channel.guild.id,
                    name=getattr(channel, "name", "unknown"),
                    type=str(getattr(channel, "type", "text")),
                    is_knowledge_channel=is_knowledge,
                )
            )
        else:
            db_channel.name = getattr(channel, "name", db_channel.name)
            db_channel.is_knowledge_channel = is_knowledge or db_channel.is_knowledge_channel

    async def archive_message(self, message: discord.Message, *, is_knowledge_channel: bool = False) -> None:
        if message.guild is None:
            return  # DMs are never archived

        async with get_session() as session:
            await self._ensure_guild(session, message.guild)
            await self._ensure_channel(session, message.channel, is_knowledge=is_knowledge_channel)

            attachment_urls = ",".join(a.url for a in message.attachments) or None
            session.add(
                Message(
                    discord_message_id=message.id,
                    guild_id=message.guild.id,
                    channel_id=message.channel.id,
                    author_id=message.author.id,
                    author_name=str(message.author),
                    content=message.content or "",
                    attachment_urls=attachment_urls,
                )
            )

    async def update_content(self, message: discord.Message) -> None:
        stmt = select(Message).where(Message.discord_message_id == message.id)
        async with get_session() as session:
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row is not None:
                row.content = message.content or ""
                row.edited_at = dt.datetime.now(dt.timezone.utc)

    async def mark_deleted(self, discord_message_id: int) -> None:
        stmt = select(Message).where(Message.discord_message_id == discord_message_id)
        async with get_session() as session:
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row is not None:
                row.is_deleted = True
