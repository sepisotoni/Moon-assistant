from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from bot.database.models_ai import AIModelConfig, AIModelHealth
from bot.repositories.ai_state_repository import ModelRegistryRepository, SettingsRepository


@dataclass
class ModelStatus:
    config: AIModelConfig
    health: AIModelHealth | None

    @property
    def display(self) -> str:
        if self.health is None:
            return "unknown"
        if self.health.is_healthy:
            lat = f"{self.health.last_latency_ms}ms" if self.health.last_latency_ms else "n/a"
            return f"healthy (latency={lat}, successes={self.health.success_count})"
        last_fail = ""
        if self.health.last_failure_at:
            delta = dt.datetime.now(dt.timezone.utc) - self.health.last_failure_at
            last_fail = f", last failure {int(delta.total_seconds())}s ago"
        return f"unhealthy ({self.health.consecutive_failures} consecutive failures{last_fail})"


class ModelRoutingService:
    """Business logic for the /aimodel family of commands.

    Deliberately thin: all actual routing happens in ModelRouter (bot/ai/model_routing.py);
    this class is the service-layer bridge that cogs call so no DB/repository logic leaks into
    cog code.
    """

    def __init__(self) -> None:
        self._registry = ModelRegistryRepository()
        self._settings_repo = SettingsRepository()

    async def list_status(self) -> list[ModelStatus]:
        configs = await self._registry.list_all()
        result = []
        for config in configs:
            health = await self._registry.get_health(config.id)
            result.append(ModelStatus(config=config, health=health))
        return result

    async def get_model_by_id(self, model_id: int) -> AIModelConfig | None:
        configs = await self._registry.list_all()
        return next((c for c in configs if c.id == model_id), None)

    async def set_override(
        self, *, guild_id: int, task_type: str, model_config_id: int, set_by: int
    ) -> None:
        await self._registry.set_override(
            guild_id=guild_id, task_type=task_type, model_config_id=model_config_id, set_by=set_by
        )

    async def clear_override(self, *, guild_id: int, task_type: str) -> None:
        await self._registry.clear_override(guild_id, task_type)

    async def get_override(self, guild_id: int, task_type: str):
        return await self._registry.get_override(guild_id, task_type)

    async def is_ai_enabled(self, guild_id: int) -> bool:
        val = await self._settings_repo.get(guild_id, "ai_enabled")
        return val != "false"

    async def set_ai_enabled(self, guild_id: int, enabled: bool, *, updated_by: int) -> None:
        await self._settings_repo.set(guild_id, "ai_enabled", "true" if enabled else "false", updated_by=updated_by)
