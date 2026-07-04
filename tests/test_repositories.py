"""Repository integration tests — run against an in-memory SQLite DB (no Postgres needed).

These tests verify the data-access layer end-to-end including real SQLAlchemy queries
(ILIKE on SQLite uses LIKE, so case-sensitivity differs; the tests are written to be
DB-agnostic where possible).
"""
from __future__ import annotations

import pytest
import pytest_asyncio

from bot.database.models_ai import ConstitutionTier, MemoryScope


@pytest.mark.asyncio
class TestConstitutionRepository:
    async def test_add_and_list_rules(self, patched_get_session):
        from bot.repositories.ai_repository import ConstitutionRepository

        repo = ConstitutionRepository()
        await repo.add(
            tier=ConstitutionTier.SERVER,
            title="No NSFW",
            rule_text="No NSFW content in any channel.",
            guild_id=1,
            created_by=100,
        )
        rules = await repo.list_active(1)
        assert any(r.title == "No NSFW" for r in rules)

    async def test_global_rules_visible_to_all_guilds(self, patched_get_session):
        from bot.repositories.ai_repository import ConstitutionRepository

        repo = ConstitutionRepository()
        await repo.add(
            tier=ConstitutionTier.PLATFORM_SAFETY,
            title="Global safety rule",
            rule_text="Platform-wide safety requirement.",
            guild_id=None,
            created_by=None,
            is_seed_rule=True,
        )
        # Rule with guild_id=None should appear for guild 999.
        rules = await repo.list_active(999)
        assert any(r.title == "Global safety rule" for r in rules)

    async def test_disable_rule_removes_from_active(self, patched_get_session):
        from bot.repositories.ai_repository import ConstitutionRepository

        repo = ConstitutionRepository()
        rule = await repo.add(
            tier=ConstitutionTier.SERVER,
            title="Rule to disable",
            rule_text="This rule will be disabled.",
            guild_id=2,
            created_by=200,
        )
        await repo.set_enabled(rule.id, is_enabled=False)
        rules = await repo.list_active(2)
        assert not any(r.id == rule.id for r in rules)

    async def test_rules_ordered_by_tier_then_priority(self, patched_get_session):
        from bot.repositories.ai_repository import ConstitutionRepository

        repo = ConstitutionRepository()
        await repo.add(tier=ConstitutionTier.TASK, title="T5", rule_text=".", guild_id=3, created_by=1, priority=1)
        await repo.add(tier=ConstitutionTier.CORE_BOT, title="T2", rule_text=".", guild_id=None, created_by=None, priority=1)
        rules = await repo.list_active(3)
        tiers = [r.tier.value for r in rules]
        assert tiers == sorted(tiers)


@pytest.mark.asyncio
class TestMemoryRepository:
    async def test_upsert_and_get(self, patched_get_session):
        from bot.repositories.ai_state_repository import MemoryRepository

        repo = MemoryRepository()
        await repo.upsert(
            guild_id=10, scope=MemoryScope.SERVER, key="fact:ip",
            value="play.test.com", expires_at=None, increment_hit=False,
        )
        entry = await repo.get(10, MemoryScope.SERVER, "fact:ip")
        assert entry is not None
        assert entry.value == "play.test.com"

    async def test_upsert_updates_existing(self, patched_get_session):
        from bot.repositories.ai_state_repository import MemoryRepository

        repo = MemoryRepository()
        await repo.upsert(guild_id=11, scope=MemoryScope.SERVER, key="fact:name",
                           value="OldServer", expires_at=None, increment_hit=False)
        await repo.upsert(guild_id=11, scope=MemoryScope.SERVER, key="fact:name",
                           value="NewServer", expires_at=None, increment_hit=False)
        entry = await repo.get(11, MemoryScope.SERVER, "fact:name")
        assert entry.value == "NewServer"

    async def test_hit_count_increments(self, patched_get_session):
        from bot.repositories.ai_state_repository import MemoryRepository

        repo = MemoryRepository()
        for _ in range(3):
            await repo.upsert(
                guild_id=12, scope=MemoryScope.OPERATIONAL, key="recurring:join_issue",
                value="Check whitelist", expires_at=None, increment_hit=True,
            )
        entry = await repo.get(12, MemoryScope.OPERATIONAL, "recurring:join_issue")
        assert entry.hit_count >= 3

    async def test_missing_key_returns_none(self, patched_get_session):
        from bot.repositories.ai_state_repository import MemoryRepository

        repo = MemoryRepository()
        entry = await repo.get(99, MemoryScope.SHORT_TERM, "nonexistent:key:xyz")
        assert entry is None


@pytest.mark.asyncio
class TestModelRegistryRepository:
    async def test_add_and_list_models(self, patched_get_session):
        from bot.repositories.ai_state_repository import ModelRegistryRepository

        repo = ModelRegistryRepository()
        await repo.add(provider="openrouter", model_name="test/model-1", priority=10)
        configs = await repo.list_all()
        assert any(c.model_name == "test/model-1" for c in configs)

    async def test_record_success_marks_healthy(self, patched_get_session):
        from bot.repositories.ai_state_repository import ModelRegistryRepository

        repo = ModelRegistryRepository()
        config = await repo.add(provider="gemini", model_name="gemini-test-1")
        await repo.record_success(config.id, latency_ms=123)
        health = await repo.get_health(config.id)
        assert health is not None
        assert health.is_healthy is True
        assert health.last_latency_ms == 123

    async def test_consecutive_failures_mark_unhealthy(self, patched_get_session):
        from bot.repositories.ai_state_repository import ModelRegistryRepository

        repo = ModelRegistryRepository()
        config = await repo.add(provider="openrouter", model_name="test/failing-model")
        for _ in range(3):
            await repo.record_failure(config.id, unhealthy_after=3)
        health = await repo.get_health(config.id)
        assert health.is_healthy is False

    async def test_success_after_failure_restores_health(self, patched_get_session):
        from bot.repositories.ai_state_repository import ModelRegistryRepository

        repo = ModelRegistryRepository()
        config = await repo.add(provider="openrouter", model_name="test/recovering-model")
        for _ in range(3):
            await repo.record_failure(config.id, unhealthy_after=3)
        await repo.record_success(config.id, latency_ms=50)
        health = await repo.get_health(config.id)
        assert health.is_healthy is True
        assert health.consecutive_failures == 0
