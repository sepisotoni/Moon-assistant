from __future__ import annotations

import datetime as dt

from bot.config import get_settings
from bot.database.models_ai import MemoryScope
from bot.repositories.ai_state_repository import MemoryRepository

settings = get_settings()


class MemoryService:
    """Short-term / server / operational memory, per the spec's three memory types.

    - short_term: conversational context (e.g. "what was !ask just discussing in this channel
      with this user"), expires after SHORT_TERM_MEMORY_TTL_SECONDS.
    - server: durable facts about the server (server IP, store URL, voting link...), no expiry
      unless the caller explicitly sets one.
    - operational: recurring issues/questions and how they were resolved, tracked via hit_count
      so the support engine can prioritize the most common resolutions. No expiry by default.
    """

    def __init__(self) -> None:
        self._repo = MemoryRepository()

    # --- short-term (conversational) ---
    async def remember_conversation_turn(self, *, guild_id: int, channel_id: int, user_id: int, summary: str) -> None:
        key = f"conversation:{channel_id}:{user_id}"
        expires_at = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=settings.short_term_memory_ttl_seconds)
        await self._repo.upsert(
            guild_id=guild_id, scope=MemoryScope.SHORT_TERM, key=key, value=summary, expires_at=expires_at,
            increment_hit=False,
        )

    async def recall_conversation_turn(self, *, guild_id: int, channel_id: int, user_id: int) -> str | None:
        key = f"conversation:{channel_id}:{user_id}"
        entry = await self._repo.get(guild_id, MemoryScope.SHORT_TERM, key)
        return entry.value if entry else None

    # --- server facts ---
    async def set_server_fact(self, *, guild_id: int, fact_key: str, value: str) -> None:
        await self._repo.upsert(
            guild_id=guild_id, scope=MemoryScope.SERVER, key=f"fact:{fact_key}", value=value, expires_at=None,
            increment_hit=False,
        )

    async def get_server_fact(self, *, guild_id: int, fact_key: str) -> str | None:
        entry = await self._repo.get(guild_id, MemoryScope.SERVER, f"fact:{fact_key}")
        return entry.value if entry else None

    async def list_server_facts(self, guild_id: int):
        return await self._repo.list_scope(guild_id, MemoryScope.SERVER)

    # --- operational memory (recurring issues/questions + resolutions) ---
    async def record_recurring(self, *, guild_id: int, topic_key: str, resolution: str) -> None:
        """Call this whenever a support/investigation flow resolves something, to build up a
        record of common issues and how they were solved. hit_count increments automatically."""
        await self._repo.upsert(
            guild_id=guild_id,
            scope=MemoryScope.OPERATIONAL,
            key=f"recurring:{topic_key}",
            value=resolution,
            expires_at=None,
            increment_hit=True,
        )

    async def get_recurring(self, *, guild_id: int, topic_key: str) -> str | None:
        entry = await self._repo.get(guild_id, MemoryScope.OPERATIONAL, f"recurring:{topic_key}")
        return entry.value if entry else None

    async def top_recurring(self, guild_id: int, limit: int = 10):
        return await self._repo.list_scope(guild_id, MemoryScope.OPERATIONAL, limit=limit)

    # --- maintenance ---
    async def purge_expired(self) -> int:
        return await self._repo.purge_expired()
