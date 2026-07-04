"""Tests for bot/ai/orchestrator.py – confidence, escalation, dual-review logic."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.ai.base import AIMessage, AIProviderError, AIResponse
from bot.ai.orchestrator import AIOrchestrator, _default_agreement


class TestDefaultAgreement:
    def test_identical_texts_agree(self):
        assert _default_agreement("The server is down.", "The server is down.")

    def test_completely_different_disagree(self):
        assert not _default_agreement("warn the user", "qwertyuiop completely different xyz")

    def test_case_insensitive(self):
        assert _default_agreement("WARN THE USER", "warn the user")


class TestConfidenceEstimation:
    def _make_orch(self):
        settings = MagicMock()
        settings.confidence_escalation_threshold = 0.55
        settings.auto_action_confidence_threshold = 0.75
        settings.dual_review_enabled = True
        return AIOrchestrator(settings=settings, router=MagicMock(), legacy_manager=None)

    def test_high_evidence_boosts_confidence(self):
        orch = self._make_orch()
        conf = orch._estimate_confidence("Here is a clear answer.", evidence_count=3)
        assert conf > 0.8

    def test_no_evidence_lowers_confidence(self):
        orch = self._make_orch()
        conf = orch._estimate_confidence("Here is an answer.", evidence_count=0)
        assert conf < 0.6

    def test_hedge_words_lower_confidence(self):
        orch = self._make_orch()
        base = orch._estimate_confidence("Sure thing.", evidence_count=3)
        hedged = orch._estimate_confidence("I'm not sure about this.", evidence_count=3)
        assert hedged < base

    def test_empty_text_gives_low_confidence(self):
        orch = self._make_orch()
        assert orch._estimate_confidence("", evidence_count=5) < 0.2


@pytest.mark.asyncio
class TestGenerateForTask:
    def _make_orch_with_mock_router(self, response_text="OK"):
        settings = MagicMock()
        settings.confidence_escalation_threshold = 0.55
        settings.auto_action_confidence_threshold = 0.75
        settings.dual_review_enabled = False

        router = AsyncMock()
        mock_config = MagicMock()
        mock_config.id = 1
        mock_config.provider = "mock"
        mock_config.model_name = "mock-model"
        router.generate = AsyncMock(return_value=(
            AIResponse(text=response_text, provider="mock", model="mock-model"),
            mock_config,
        ))

        orch = AIOrchestrator(settings=settings, router=router, legacy_manager=None)
        # Skip actual DB write in tests
        orch._decision_log = AsyncMock()
        orch._decision_log.record = AsyncMock()
        return orch

    async def test_returns_decision_with_text(self):
        orch = self._make_orch_with_mock_router("Hello there")
        messages = [AIMessage(role="user", content="test")]
        decision = await orch.generate_for_task("support", messages, guild_id=1)
        assert decision.text == "Hello there"
        assert decision.provider == "mock"

    async def test_escalate_flag_set_when_below_threshold(self):
        orch = self._make_orch_with_mock_router("I'm not sure about this at all.")
        messages = [AIMessage(role="user", content="test")]
        decision = await orch.generate_for_task("support", messages, guild_id=1, evidence_count=0)
        assert decision.escalate is True

    async def test_no_escalate_when_high_confidence(self):
        orch = self._make_orch_with_mock_router("The answer is definitively X.")
        messages = [AIMessage(role="user", content="test")]
        decision = await orch.generate_for_task("support", messages, guild_id=1, evidence_count=5)
        assert decision.escalate is False

    async def test_falls_back_to_legacy_manager_when_router_fails(self):
        settings = MagicMock()
        settings.confidence_escalation_threshold = 0.55
        settings.auto_action_confidence_threshold = 0.75
        settings.dual_review_enabled = False

        from bot.ai.model_routing import NoHealthyModelError
        router = AsyncMock()
        router.generate = AsyncMock(side_effect=NoHealthyModelError("no models"))

        legacy = AsyncMock()
        legacy.generate = AsyncMock(return_value=AIResponse(text="Legacy response", provider="legacy", model="fallback"))

        orch = AIOrchestrator(settings=settings, router=router, legacy_manager=legacy)
        orch._decision_log = AsyncMock()
        orch._decision_log.record = AsyncMock()

        decision = await orch.generate_for_task("support", [AIMessage(role="user", content="test")], guild_id=1)
        assert decision.text == "Legacy response"
