from __future__ import annotations

import datetime as dt
import logging
import time

from bot.ai.base import AIMessage, AIProvider, AIProviderError, AIResponse
from bot.ai.gemini_provider import GeminiProvider
from bot.ai.openrouter_provider import OpenRouterProvider
from bot.config import Settings
from bot.database.models_ai import AIModelConfig
from bot.repositories.ai_state_repository import ModelRegistryRepository

logger = logging.getLogger(__name__)


def _ensure_utc(ts: dt.datetime) -> dt.datetime:
    """Ensure a datetime is timezone-aware (UTC).

    asyncpg returns TIMESTAMP WITH TIME ZONE as timezone-aware, but some driver
    or ORM version combinations may return a naive datetime that is actually UTC.
    Rather than crash, we treat any naive value as UTC.
    """
    if ts.tzinfo is None:
        return ts.replace(tzinfo=dt.timezone.utc)
    return ts


class NoHealthyModelError(AIProviderError):
    """Raised when every candidate model for a task is unhealthy or unconfigured."""


class ProviderFactory:
    """Builds (and caches) AIProvider instances for a given (provider, model_name) pair."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._cache: dict[tuple[str, str], AIProvider] = {}

    def get(self, provider: str, model_name: str) -> AIProvider:
        key = (provider, model_name)
        if key in self._cache:
            return self._cache[key]

        if provider == "openrouter":
            if not self._settings.openrouter_api_key:
                raise AIProviderError(
                    "OPENROUTER_API_KEY is not set in .env — add your OpenRouter key to enable AI responses."
                )
            instance: AIProvider = OpenRouterProvider(
                api_key=self._settings.openrouter_api_key,
                model=model_name,
                base_url=self._settings.openrouter_base_url,
            )
        elif provider == "gemini":
            if not self._settings.gemini_api_key:
                raise AIProviderError(
                    "GEMINI_API_KEY is not set in .env — add your Gemini key to enable AI responses."
                )
            instance = GeminiProvider(api_key=self._settings.gemini_api_key, model=model_name)
        else:
            raise AIProviderError(f"Unknown AI provider: {provider!r}")

        self._cache[key] = instance
        return instance

    async def close_all(self) -> None:
        for instance in self._cache.values():
            await instance.close()


class ModelRouter:
    """Selects which (provider, model) to use for a task, with health-aware failover.

    Selection order:
      1. A guild's manual override for this task ("/aimodel set"), if its model is healthy.
      2. Enabled AIModelConfig rows eligible for this task, ordered by priority, skipping
         models marked unhealthy (unless their cooldown window has elapsed — half-open retry).
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._registry = ModelRegistryRepository()
        self._factory = ProviderFactory(settings)

    async def seed_defaults(self) -> None:
        """Idempotently populate ai_model_configs from the .env candidate lists, if empty."""
        existing = await self._registry.list_all()
        if existing:
            return
        for i, model_name in enumerate(self._settings.openrouter_candidate_model_list):
            await self._registry.add(provider="openrouter", model_name=model_name, priority=10 + i, is_free=True)
        for i, model_name in enumerate(self._settings.gemini_candidate_model_list):
            await self._registry.add(provider="gemini", model_name=model_name, priority=100 + i, is_free=True)
        logger.info("Seeded default AI model registry from .env candidate lists.")

    async def _is_usable(self, config: AIModelConfig) -> bool:
        health = await self._registry.get_health(config.id)
        if health is None or health.is_healthy:
            return True
        if health.last_failure_at is None:
            return True

        # FIX: ensure both sides of the subtraction are timezone-aware.
        # asyncpg can return naive datetimes from timestamptz columns in some
        # driver/ORM combinations; _ensure_utc() normalises them to UTC.
        last_failure = _ensure_utc(health.last_failure_at)
        elapsed = (dt.datetime.now(dt.timezone.utc) - last_failure).total_seconds()
        return elapsed >= self._settings.model_health_cooldown_seconds  # half-open retry

    async def _candidates(self, guild_id: int, task_type: str) -> list[AIModelConfig]:
        configs = await self._registry.list_enabled_for_task(task_type)
        if not configs:
            return []

        override = await self._registry.get_override(guild_id, task_type)
        ordered = sorted(configs, key=lambda c: c.priority)
        if override is not None:
            pinned = [c for c in ordered if c.id == override.model_config_id]
            rest = [c for c in ordered if c.id != override.model_config_id]
            ordered = pinned + rest

        usable: list[AIModelConfig] = []
        for c in ordered:
            if await self._is_usable(c):
                usable.append(c)
        return usable

    async def generate(
        self,
        *,
        guild_id: int,
        task_type: str,
        messages: list[AIMessage],
        exclude_model_ids: set[int] | None = None,
        **kwargs,
    ) -> tuple[AIResponse, AIModelConfig]:
        exclude_model_ids = exclude_model_ids or set()
        candidates = [c for c in await self._candidates(guild_id, task_type) if c.id not in exclude_model_ids]
        if not candidates:
            raise NoHealthyModelError(
                f"No healthy model available for task '{task_type}'. "
                "Check that OPENROUTER_API_KEY and/or GEMINI_API_KEY are set in .env, "
                "and run `!ai-status` to see model health."
            )

        last_error: Exception | None = None
        for config in candidates:
            try:
                provider = self._factory.get(config.provider, config.model_name)
            except AIProviderError as exc:
                # Missing API key for this provider — skip without marking unhealthy.
                logger.debug("Skipping %s/%s: %s", config.provider, config.model_name, exc)
                last_error = exc
                continue

            start = time.monotonic()
            try:
                response = await provider.generate(messages, **kwargs)
            except AIProviderError as exc:
                last_error = exc
                await self._registry.record_failure(
                    config.id, unhealthy_after=self._settings.model_unhealthy_after_failures
                )
                logger.warning("Model %s/%s failed for task %s: %s", config.provider, config.model_name, task_type, exc)
                continue
            else:
                latency_ms = int((time.monotonic() - start) * 1000)
                await self._registry.record_success(config.id, latency_ms=latency_ms)
                return response, config

        raise NoHealthyModelError(
            f"All candidate models failed for task '{task_type}': {last_error}"
        )

    async def close(self) -> None:
        await self._factory.close_all()
