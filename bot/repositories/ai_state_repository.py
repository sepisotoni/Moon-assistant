from __future__ import annotations

import datetime as dt

from sqlalchemy import select

from bot.database.models_ai import (
    AIModelConfig,
    AIModelHealth,
    GuildSetting,
    MemoryEntry,
    MemoryScope,
    TaskRouteOverride,
)
from bot.database.session import get_session


class MemoryRepository:
    """Data access for the short-term / server / operational memory store."""

    async def get(self, guild_id: int, scope: MemoryScope, key: str) -> MemoryEntry | None:
        async with get_session() as session:
            stmt = select(MemoryEntry).where(
                MemoryEntry.guild_id == guild_id, MemoryEntry.scope == scope, MemoryEntry.key == key
            )
            result = await session.execute(stmt)
            entry = result.scalar_one_or_none()
            if entry is not None and entry.expires_at is not None:
                if entry.expires_at < dt.datetime.now(dt.timezone.utc):
                    return None  # expired; caller treats this as a miss (lazy expiry)
            return entry

    async def upsert(
        self,
        *,
        guild_id: int,
        scope: MemoryScope,
        key: str,
        value: str,
        expires_at: dt.datetime | None,
        increment_hit: bool = True,
    ) -> MemoryEntry:
        async with get_session() as session:
            stmt = select(MemoryEntry).where(
                MemoryEntry.guild_id == guild_id, MemoryEntry.scope == scope, MemoryEntry.key == key
            )
            result = await session.execute(stmt)
            entry = result.scalar_one_or_none()
            if entry is None:
                entry = MemoryEntry(guild_id=guild_id, scope=scope, key=key, value=value, expires_at=expires_at)
                session.add(entry)
            else:
                entry.value = value
                entry.expires_at = expires_at
                if increment_hit:
                    entry.hit_count += 1
            await session.flush()
            await session.refresh(entry)
            return entry

    async def list_scope(self, guild_id: int, scope: MemoryScope, limit: int = 50) -> list[MemoryEntry]:
        async with get_session() as session:
            stmt = (
                select(MemoryEntry)
                .where(MemoryEntry.guild_id == guild_id, MemoryEntry.scope == scope)
                .order_by(MemoryEntry.hit_count.desc())
                .limit(limit)
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def purge_expired(self) -> int:
        """Sweep expired entries. Returns the number removed. Safe to call periodically."""
        from sqlalchemy import delete

        now = dt.datetime.now(dt.timezone.utc)
        async with get_session() as session:
            result = await session.execute(
                delete(MemoryEntry).where(MemoryEntry.expires_at.is_not(None), MemoryEntry.expires_at < now)
            )
            return result.rowcount or 0


class ModelRegistryRepository:
    """Data access for AI model configs, their health, and per-task routing overrides."""

    async def list_enabled_for_task(self, task_type: str) -> list[AIModelConfig]:
        async with get_session() as session:
            stmt = select(AIModelConfig).where(AIModelConfig.is_enabled.is_(True)).order_by(
                AIModelConfig.priority.asc()
            )
            result = await session.execute(stmt)
            rows = list(result.scalars().all())
        return [
            r for r in rows if r.task_types == "*" or task_type in {t.strip() for t in r.task_types.split(",")}
        ]

    async def list_all(self) -> list[AIModelConfig]:
        async with get_session() as session:
            result = await session.execute(select(AIModelConfig).order_by(AIModelConfig.priority.asc()))
            return list(result.scalars().all())

    async def add(
        self, *, provider: str, model_name: str, task_types: str = "*", priority: int = 100, is_free: bool = True
    ) -> AIModelConfig:
        async with get_session() as session:
            config = AIModelConfig(
                provider=provider, model_name=model_name, task_types=task_types, priority=priority, is_free=is_free
            )
            session.add(config)
            await session.flush()
            await session.refresh(config)
            return config

    async def get_health(self, model_config_id: int) -> AIModelHealth | None:
        async with get_session() as session:
            stmt = select(AIModelHealth).where(AIModelHealth.model_config_id == model_config_id)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def record_success(self, model_config_id: int, *, latency_ms: int) -> None:
        async with get_session() as session:
            stmt = select(AIModelHealth).where(AIModelHealth.model_config_id == model_config_id)
            result = await session.execute(stmt)
            health = result.scalar_one_or_none()
            now = dt.datetime.now(dt.timezone.utc)
            if health is None:
                session.add(
                    AIModelHealth(
                        model_config_id=model_config_id,
                        success_count=1,
                        last_success_at=now,
                        last_latency_ms=latency_ms,
                        is_healthy=True,
                    )
                )
            else:
                health.success_count += 1
                health.consecutive_failures = 0
                health.last_success_at = now
                health.last_latency_ms = latency_ms
                health.is_healthy = True

    async def record_failure(self, model_config_id: int, *, unhealthy_after: int) -> None:
        async with get_session() as session:
            stmt = select(AIModelHealth).where(AIModelHealth.model_config_id == model_config_id)
            result = await session.execute(stmt)
            health = result.scalar_one_or_none()
            now = dt.datetime.now(dt.timezone.utc)
            if health is None:
                session.add(
                    AIModelHealth(
                        model_config_id=model_config_id,
                        failure_count=1,
                        consecutive_failures=1,
                        last_failure_at=now,
                        is_healthy=unhealthy_after > 1,
                    )
                )
            else:
                health.failure_count += 1
                health.consecutive_failures += 1
                health.last_failure_at = now
                if health.consecutive_failures >= unhealthy_after:
                    health.is_healthy = False

    async def get_override(self, guild_id: int, task_type: str) -> TaskRouteOverride | None:
        async with get_session() as session:
            stmt = select(TaskRouteOverride).where(
                TaskRouteOverride.guild_id == guild_id, TaskRouteOverride.task_type == task_type
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def set_override(self, *, guild_id: int, task_type: str, model_config_id: int, set_by: int) -> None:
        async with get_session() as session:
            stmt = select(TaskRouteOverride).where(
                TaskRouteOverride.guild_id == guild_id, TaskRouteOverride.task_type == task_type
            )
            result = await session.execute(stmt)
            existing = result.scalar_one_or_none()
            if existing is None:
                session.add(
                    TaskRouteOverride(
                        guild_id=guild_id, task_type=task_type, model_config_id=model_config_id, set_by=set_by
                    )
                )
            else:
                existing.model_config_id = model_config_id
                existing.set_by = set_by

    async def clear_override(self, guild_id: int, task_type: str) -> None:
        from sqlalchemy import delete

        async with get_session() as session:
            await session.execute(
                delete(TaskRouteOverride).where(
                    TaskRouteOverride.guild_id == guild_id, TaskRouteOverride.task_type == task_type
                )
            )


class SettingsRepository:
    """Data access for generic per-guild key/value settings (AI kill switch, maintenance mode...)."""

    async def get(self, guild_id: int, key: str) -> str | None:
        async with get_session() as session:
            stmt = select(GuildSetting).where(GuildSetting.guild_id == guild_id, GuildSetting.key == key)
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            return row.value if row is not None else None

    async def set(self, guild_id: int, key: str, value: str, *, updated_by: int | None = None) -> None:
        async with get_session() as session:
            stmt = select(GuildSetting).where(GuildSetting.guild_id == guild_id, GuildSetting.key == key)
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row is None:
                session.add(GuildSetting(guild_id=guild_id, key=key, value=value, updated_by=updated_by))
            else:
                row.value = value
                row.updated_by = updated_by
