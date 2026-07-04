"""Tests for moderation intelligence: toxicity heuristic, auto-delete, repeated offender."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.moderation.intelligence_service import (
    ModerationIntelligenceService,
    detect_toxicity_heuristic,
)


class TestToxicityHeuristic:
    @pytest.mark.parametrize("text", [
        "kill yourself",
        "kys right now",
        "go die please",
        "I know where you live",
        "I'll find you",
        "I'm going to dox you",
    ])
    def test_toxic_text_detected(self, text):
        result = detect_toxicity_heuristic(text)
        assert result is not None
        assert isinstance(result, str)

    @pytest.mark.parametrize("text", [
        "Hello, how are you?",
        "Can I join the server?",
        "What is the server IP?",
        "I was warned unfairly",
    ])
    def test_clean_text_not_flagged(self, text):
        assert detect_toxicity_heuristic(text) is None

    def test_case_insensitive(self):
        assert detect_toxicity_heuristic("KYS") is not None
        assert detect_toxicity_heuristic("KILL YOURSELF") is not None


class TestSafeParse:
    def _svc(self):
        orch = AsyncMock()
        svc = ModerationIntelligenceService(orchestrator=orch)
        return svc

    def test_clean_json_parsed(self):
        svc = self._svc()
        result = svc._safe_parse('{"risk_score":0.8,"confidence":0.9,"recommended_action":"warn","evidence_summary":"test","timeout_minutes":null}')
        assert result["recommended_action"] == "warn"
        assert result["risk_score"] == 0.8

    def test_json_with_code_fences_stripped(self):
        svc = self._svc()
        raw = '```json\n{"risk_score":0.5,"confidence":0.6,"recommended_action":"escalate","evidence_summary":"test","timeout_minutes":null}\n```'
        result = svc._safe_parse(raw)
        assert result["recommended_action"] == "escalate"

    def test_unparseable_returns_escalate(self):
        svc = self._svc()
        result = svc._safe_parse("I cannot analyze this.")
        assert result["recommended_action"] == "escalate"
        assert result["confidence"] == 0.3


@pytest.mark.asyncio
class TestRepeatOffender:
    async def test_below_threshold_returns_false(self, patched_get_session):
        """Fewer warnings than the threshold → not a repeat offender."""
        result = await ModerationIntelligenceService._static_repeat_check(
            guild_id=1, user_id=42
        )
        # Fresh DB: no warnings → False
        assert result is False

    async def test_auto_action_guard_blocks_kick(self):
        """maybe_auto_apply must raise ForbiddenActionError for 'kick'."""
        from bot.moderation.action_guard import ForbiddenActionError

        orch = AsyncMock()
        svc = ModerationIntelligenceService(orchestrator=orch)

        from bot.ai.orchestrator import AIDecision
        decision = AIDecision(
            text="kick", confidence=0.99, evidence_count=1,
            retrieval_summary=None, provider="mock", model="mock",
            agreement=True, escalate=False,
        )
        parsed = {"recommended_action": "kick", "evidence_summary": "test", "report_id": None}
        guild = MagicMock()
        moderator = MagicMock()
        target = MagicMock()

        with pytest.raises(ForbiddenActionError):
            await svc.maybe_auto_apply(
                guild=guild, moderator=moderator, target=target,
                decision=decision, parsed=parsed,
            )
