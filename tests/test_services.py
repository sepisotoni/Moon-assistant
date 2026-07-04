"""Service-layer integration tests using the in-memory SQLite DB fixture."""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
class TestMemoryService:
    async def test_set_and_recall_server_fact(self, patched_get_session):
        from bot.services.memory_service import MemoryService

        svc = MemoryService()
        await svc.set_server_fact(guild_id=1, fact_key="server_ip", value="play.example.com")
        result = await svc.get_server_fact(guild_id=1, fact_key="server_ip")
        assert result == "play.example.com"

    async def test_server_fact_isolated_by_guild(self, patched_get_session):
        from bot.services.memory_service import MemoryService

        svc = MemoryService()
        await svc.set_server_fact(guild_id=2, fact_key="ip", value="guild2.example.com")
        result = await svc.get_server_fact(guild_id=3, fact_key="ip")
        assert result is None

    async def test_short_term_remember_and_recall(self, patched_get_session):
        from bot.services.memory_service import MemoryService

        svc = MemoryService()
        await svc.remember_conversation_turn(
            guild_id=4, channel_id=100, user_id=200, summary="Q: ip?\nA: play.test.com"
        )
        result = await svc.recall_conversation_turn(guild_id=4, channel_id=100, user_id=200)
        assert result is not None
        assert "play.test.com" in result

    async def test_short_term_isolated_by_channel_and_user(self, patched_get_session):
        from bot.services.memory_service import MemoryService

        svc = MemoryService()
        await svc.remember_conversation_turn(
            guild_id=5, channel_id=10, user_id=20, summary="some context"
        )
        # Different channel — should not see the memory
        result = await svc.recall_conversation_turn(guild_id=5, channel_id=99, user_id=20)
        assert result is None

    async def test_recurring_hit_count_increments(self, patched_get_session):
        from bot.services.memory_service import MemoryService

        svc = MemoryService()
        for _ in range(4):
            await svc.record_recurring(guild_id=6, topic_key="join_issue", resolution="Check whitelist")
        top = await svc.top_recurring(6)
        assert any(e.hit_count >= 4 for e in top)

    async def test_purge_expired_removes_entries(self, patched_get_session):
        import datetime as dt
        from bot.repositories.ai_state_repository import MemoryRepository
        from bot.database.models_ai import MemoryScope

        repo = MemoryRepository()
        # Insert an already-expired entry directly
        past = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)
        await repo.upsert(
            guild_id=7, scope=MemoryScope.SHORT_TERM, key="expired:test:key",
            value="old value", expires_at=past, increment_hit=False,
        )
        from bot.services.memory_service import MemoryService
        svc = MemoryService()
        removed = await svc.purge_expired()
        assert removed >= 1


@pytest.mark.asyncio
class TestConstitutionService:
    async def test_ensure_seeded_is_idempotent(self, patched_get_session):
        """Calling ensure_seeded twice should not raise or create duplicate rules."""
        from bot.ai.constitution_service import ConstitutionService

        svc = ConstitutionService()
        await svc.ensure_seeded()
        await svc.ensure_seeded()  # second call must be a no-op
        rules = await svc.list_rules(None)
        titles = [r.title for r in rules]
        assert len(titles) == len(set(titles)), "Duplicate seed rules were created"

    async def test_build_system_prompt_contains_base(self, patched_get_session):
        from bot.ai.constitution_service import ConstitutionService

        svc = ConstitutionService()
        prompt = await svc.build_system_prompt(guild_id=10, base_prompt="You are a helpful bot.")
        assert "You are a helpful bot." in prompt

    async def test_server_rule_appears_in_prompt(self, patched_get_session):
        from bot.ai.constitution_service import ConstitutionService

        svc = ConstitutionService()
        await svc.add_server_rule(
            guild_id=20, title="No Profanity", rule_text="Never use profanity.", created_by=1
        )
        prompt = await svc.build_system_prompt(guild_id=20, base_prompt="Base prompt.")
        assert "No Profanity" in prompt or "Never use profanity" in prompt

    async def test_cache_invalidated_after_add_rule(self, patched_get_session):
        from bot.ai.constitution_service import ConstitutionService

        svc = ConstitutionService()
        await svc.build_system_prompt(guild_id=30, base_prompt="Base.")
        assert 30 in svc._cache
        await svc.add_server_rule(guild_id=30, title="New rule", rule_text="Test.", created_by=1)
        assert 30 not in svc._cache


@pytest.mark.asyncio
class TestKnowledgeLearningService:
    async def test_list_pending_returns_pending_only(self, patched_get_session):
        from bot.knowledge.learning_service import KnowledgeLearningService
        from bot.repositories.knowledge_repository import KnowledgeReviewRepository

        repo = KnowledgeReviewRepository()
        correction = await repo.create_correction(
            guild_id=100, channel_id=1, channel_name="ai-faq",
            original_entry_id=9999,  # hypothetical existing entry
            discord_message_id=88888, author_id=1, author_name="Staff",
            content="Updated FAQ answer",
        )
        svc = KnowledgeLearningService()
        pending = await svc.list_pending(100)
        assert any(p.id == correction.id for p in pending)

    async def test_approve_sets_status(self, patched_get_session):
        from bot.knowledge.learning_service import KnowledgeLearningService
        from bot.repositories.knowledge_repository import KnowledgeReviewRepository
        from bot.database.models import KnowledgeReviewStatus

        repo = KnowledgeReviewRepository()
        entry = await repo.create_correction(
            guild_id=101, channel_id=2, channel_name="ai-faq",
            original_entry_id=9998, discord_message_id=77777,
            author_id=2, author_name="Staff2", content="A correction",
        )
        svc = KnowledgeLearningService()
        approved = await svc.approve(entry.id, reviewed_by=999)
        assert approved is not None
        assert approved.review_status == KnowledgeReviewStatus.APPROVED
        assert approved.reviewed_by == 999

    async def test_reject_sets_status(self, patched_get_session):
        from bot.knowledge.learning_service import KnowledgeLearningService
        from bot.repositories.knowledge_repository import KnowledgeReviewRepository
        from bot.database.models import KnowledgeReviewStatus

        repo = KnowledgeReviewRepository()
        entry = await repo.create_correction(
            guild_id=102, channel_id=3, channel_name="ai-news",
            original_entry_id=9997, discord_message_id=66666,
            author_id=3, author_name="Staff3", content="A bad correction",
        )
        svc = KnowledgeLearningService()
        rejected = await svc.reject(entry.id, reviewed_by=888)
        assert rejected is not None
        assert rejected.review_status == KnowledgeReviewStatus.REJECTED
