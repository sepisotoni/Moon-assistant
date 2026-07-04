"""Unit tests for SupportEngine and InvestigationService using a mocked orchestrator."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.ai.intent_service import Intent
from bot.ai.orchestrator import AIDecision


def _make_decision(text="Answer.", confidence=0.85, escalate=False):
    return AIDecision(
        text=text, confidence=confidence, evidence_count=2,
        retrieval_summary="mock", provider="mock", model="mock-model",
        escalate=escalate,
    )


@pytest.fixture
def mock_guild():
    guild = MagicMock()
    guild.id = 1
    guild.text_channels = []
    guild.threads = []
    return guild


@pytest.fixture
def mock_member(mock_guild):
    member = MagicMock()
    member.id = 42
    member.display_name = "Tester"
    member.bot = False
    member.roles = []
    member.guild = mock_guild
    member.guild_permissions = MagicMock(administrator=False)
    return member


@pytest.mark.asyncio
class TestSupportEngine:
    def _make_engine(self, decision=None):
        from bot.ai.constitution_service import ConstitutionService
        from bot.ai.intent_service import IntentDetectionService
        from bot.services.support_engine import SupportEngine

        orch = AsyncMock()
        orch.generate_for_task = AsyncMock(return_value=decision or _make_decision())

        constitution = AsyncMock(spec=ConstitutionService)
        constitution.build_system_prompt = AsyncMock(return_value="System prompt.")

        intent_svc = MagicMock(spec=IntentDetectionService)
        from bot.ai.intent_service import IntentResult
        intent_svc.detect = AsyncMock(return_value=IntentResult(
            intent=Intent.SERVER_IP, confidence=0.9, source="keyword"
        ))

        engine = SupportEngine(orchestrator=orch, constitution=constitution, intent_service=intent_svc)
        # Stub out retriever and search to avoid DB calls in unit tests
        engine._knowledge = AsyncMock()
        engine._knowledge.search = AsyncMock(return_value=[])
        engine._search = AsyncMock()
        engine._search.search = AsyncMock(return_value=[])
        engine._memory = AsyncMock()
        engine._memory.get_recurring = AsyncMock(return_value=None)
        engine._memory.record_recurring = AsyncMock()
        engine._memory.remember_conversation_turn = AsyncMock()
        return engine

    async def test_answer_returns_decision_and_intent(self, mock_guild, mock_member):
        engine = self._make_engine(_make_decision("The IP is play.test.com"))
        decision, intent = await engine.answer(
            guild=mock_guild, member=mock_member, channel_id=1, question="what is the ip"
        )
        assert "play.test.com" in decision.text
        assert isinstance(intent, Intent)

    async def test_answer_records_conversation_memory(self, mock_guild, mock_member):
        engine = self._make_engine()
        await engine.answer(guild=mock_guild, member=mock_member, channel_id=1, question="what is the ip")
        engine._memory.remember_conversation_turn.assert_called_once()

    async def test_escalated_answer_does_not_record_recurring(self, mock_guild, mock_member):
        engine = self._make_engine(_make_decision(escalate=True))
        await engine.answer(guild=mock_guild, member=mock_member, channel_id=1, question="??")
        engine._memory.record_recurring.assert_not_called()


@pytest.mark.asyncio
class TestInvestigationService:
    def _make_svc(self, decision=None):
        from bot.services.investigation_service import InvestigationService

        orch = AsyncMock()
        orch.generate_for_task = AsyncMock(return_value=decision or _make_decision())
        svc = InvestigationService(orchestrator=orch)
        svc._repo = AsyncMock()
        svc._repo.create_investigation = AsyncMock(return_value=MagicMock(id=1))
        svc._repo.add_finding = AsyncMock()
        return svc

    async def test_investigate_returns_decision(self, mock_guild, mock_member):
        svc = self._make_svc()

        # Stub all tools to return trivial findings
        with patch("bot.services.investigation_service.tools_for_intent", return_value=[]):
            decision = await svc.investigate(
                guild=mock_guild,
                requester=mock_member,
                target_user_id=None,
                intent=Intent.JOIN_ISSUE,
                question="why cant i join",
            )
        assert decision is not None
        assert isinstance(decision.text, str)

    async def test_investigate_persists_record(self, mock_guild, mock_member):
        svc = self._make_svc()
        with patch("bot.services.investigation_service.tools_for_intent", return_value=[]):
            await svc.investigate(
                guild=mock_guild, requester=mock_member,
                target_user_id=None, intent=Intent.INVESTIGATION,
                question="investigate user",
            )
        svc._repo.create_investigation.assert_called_once()
