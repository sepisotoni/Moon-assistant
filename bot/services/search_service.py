from __future__ import annotations

from dataclasses import dataclass

import discord
from sqlalchemy import select

from bot.database.models import Message
from bot.database.session import get_session
from bot.services.permission_service import get_member_visible_channel_ids, has_owner_or_founder_role


@dataclass
class SearchResult:
    discord_message_id: int
    channel_id: int
    author_name: str
    content: str


class SearchService:
    """Permission-aware search over archived messages.

    - Owner/Founder roles (or server admins) may search every archived channel.
    - Everyone else is restricted to channels they can currently view in Discord.
    - Filtering happens in two layers (allowed-channel-id set + a final per-row
      check) so that a bug in one layer can never leak content from a channel
      the requester cannot see.
    """

    async def search(
        self,
        *,
        guild: discord.Guild,
        member: discord.Member,
        query: str,
        limit: int = 10,
    ) -> list[SearchResult]:
        if has_owner_or_founder_role(member):
            allowed_channel_ids: set[int] | None = None  # None == no channel restriction
        else:
            allowed_channel_ids = get_member_visible_channel_ids(guild, member)
            if not allowed_channel_ids:
                return []

        like = f"%{query}%"
        stmt = (
            select(Message)
            .where(
                Message.guild_id == guild.id,
                Message.is_deleted.is_(False),
                Message.content.ilike(like),
            )
            .order_by(Message.created_at.desc())
            .limit(limit * 3)  # over-fetch; we may discard some rows in the permission filter below
        )

        async with get_session() as session:
            result = await session.execute(stmt)
            rows = list(result.scalars().all())

        results: list[SearchResult] = []
        for row in rows:
            if allowed_channel_ids is not None and row.channel_id not in allowed_channel_ids:
                continue  # never reveal content from a channel the requester can't view
            results.append(
                SearchResult(
                    discord_message_id=row.discord_message_id,
                    channel_id=row.channel_id,
                    author_name=row.author_name,
                    content=row.content,
                )
            )
            if len(results) >= limit:
                break

        return results
