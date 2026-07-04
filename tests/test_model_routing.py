"""Tests for model_routing.py: timezone fix, missing-key skip, NoHealthyModelError."""
from __future__ import annotations

import datetime as dt
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.ai.model_routing import NoHealthyModelError, _ensure_utc


class TestEnsureUtc:
    def test_aware_datetime_unchanged(self):
        ts = dt.datetime(2025, 1, 1, 12, 0, 0, tzinfo=dt.timezone.utc)
        assert _ensure_utc(ts) is ts

    def test_naive_datetime_gets_utc(self):
        naive = dt.datetime(2025, 1, 1, 12, 0, 0)
        result = _ensure_utc(naive)
        assert result.tzinfo == dt.timezone.utc
        assert result.replace(tzinfo=None) == naive

    def test_subtraction_no_longer_raises(self):
        """The original crash: offset-naive minus offset-aware."""
        naive = dt.datetime(2025, 1, 1, 12, 0, 0)
        aware = dt.datetime.now(dt.timezone.utc)
        # Before the fix this would raise TypeError; now it must not.
        delta = aware - _ensure_utc(naive)
        assert delta.total_seconds() > 0


@pytest.mark.asyncio
class TestModelRouterMissingKey:
    """ModelRouter should skip a model whose provider key is missing, not mark it unhealthy."""

    def _make_router(self, openrouter_key: str = "", gemini_key: str = ""):
        from bot.ai.model_routing import ModelRouter
        settings = MagicMock()
        settings.openrouter_api_key = openrouter_key
        settings.gemini_api_key = gemini_key
        settings.openrouter_base_url = "https://openrouter.ai/api/v1"
        settings.model_unhealthy_after_failures = 3
        settings.model_health_cooldown_seconds = 300
        return ModelRouter(settings)

    async def test_no_keys_raises_no_healthy_model(self):
        router = self._make_router()
        registry_mock = AsyncMock()

        config = MagicMock()
        config.id = 1
        config.provider = "openrouter"
        config.model_name = "test/model"
        config.priority = 10
        config.task_types = "*"
        config.is_enabled = True

        registry_mock.list_enabled_for_task = AsyncMock(return_value=[config])
        registry_mock.get_health = AsyncMock(return_value=None)   # never called
        registry_mock.get_override = AsyncMock(return_value=None)
        registry_mock.record_failure = AsyncMock()

        router._registry = registry_mock

        from bot.ai.base import AIMessage
        with pytest.raises(NoHealthyModelError):
            await router.generate(
                guild_id=1,
                task_type="support",
                messages=[AIMessage(role="user", content="test")],
            )

        # Missing key must NOT call record_failure (the model isn't broken, the key is absent).
        registry_mock.record_failure.assert_not_called()


@pytest.mark.asyncio
class TestIsUsableTimezone:
    """_is_usable must not raise TypeError when last_failure_at is naive."""

    async def test_naive_last_failure_does_not_crash(self):
        from bot.ai.model_routing import ModelRouter

        settings = MagicMock()
        settings.model_health_cooldown_seconds = 300

        router = ModelRouter(settings)
        router._registry = AsyncMock()

        # Simulate an unhealthy model with a naive (non-UTC-aware) last_failure_at
        health = MagicMock()
        health.is_healthy = False
        health.last_failure_at = dt.datetime(2025, 1, 1, 0, 0, 0)  # naive — the original crash

        config = MagicMock()
        config.id = 99

        router._registry.get_health = AsyncMock(return_value=health)

        # Must not raise TypeError
        result = await router._is_usable(config)
        assert isinstance(result, bool)
