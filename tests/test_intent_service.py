"""Tests for bot/ai/intent_service.py – keyword + fuzzy intent classification."""
from __future__ import annotations

import pytest

from bot.ai.intent_service import Intent, IntentDetectionService


@pytest.fixture
def svc():
    # No orchestrator wired; keyword-only mode is sufficient for these tests.
    return IntentDetectionService(orchestrator=None)


class TestKeywordDetection:
    @pytest.mark.parametrize("text,expected", [
        ("what is the server ip", Intent.SERVER_IP),
        ("what is the ip address", Intent.SERVER_IP),
        ("i cant join the server", Intent.JOIN_ISSUE),
        ("cant join", Intent.JOIN_ISSUE),
        ("i'm not whitelisted", Intent.WHITELIST_ISSUE),
        ("why was i banned", Intent.PUNISHMENT_QUESTION),
        ("i want to link my account", Intent.ACCOUNT_LINKING),
        ("what are the rules", Intent.RULES_QUESTION),
        ("where is the vote link", Intent.VOTING_QUESTION),
        ("i want to buy something in the store", Intent.STORE_QUESTION),
        ("i want to report a player", Intent.REPORT_PLAYER),
        ("please translate this", Intent.TRANSLATION),
        ("can you summarize this", Intent.SUMMARIZATION),
        ("explain what the argument is about", Intent.EXPLANATION),
        ("investigate why this user was punished", Intent.INVESTIGATION),
    ])
    def test_clear_keyword_matches(self, svc, text, expected):
        result = svc.detect_keyword_only(text)
        assert result.intent == expected
        assert result.confidence > 0.5

    def test_unknown_text_returns_general_question(self, svc):
        result = svc.detect_keyword_only("qwertyuiop totally random nonsense xyz")
        assert result.intent == Intent.GENERAL_QUESTION

    def test_confidence_is_high_for_exact_match(self, svc):
        result = svc.detect_keyword_only("what is the server ip")
        assert result.confidence == 1.0

    def test_source_is_keyword(self, svc):
        result = svc.detect_keyword_only("server ip address")
        assert result.source == "keyword"


class TestFuzzyMatching:
    """Fuzzy matching should tolerate common misspellings."""

    @pytest.mark.parametrize("typo", [
        "whitlisted",   # whitelist misspelling
        "whitelsted",
    ])
    def test_whitelist_fuzzy(self, svc, typo):
        result = svc.detect_keyword_only(f"i am not {typo}")
        # Fuzzy should still produce the correct intent, even if at lower confidence.
        assert result.intent == Intent.WHITELIST_ISSUE

    def test_result_has_intent_and_confidence(self, svc):
        result = svc.detect_keyword_only("any text")
        assert hasattr(result, "intent")
        assert hasattr(result, "confidence")
        assert 0.0 <= result.confidence <= 1.0


class TestAsyncDetect:
    @pytest.mark.asyncio
    async def test_async_detect_no_ai_fallback(self, svc):
        """With no orchestrator, detect() should return the keyword result even below threshold."""
        result = await svc.detect("what is the server ip")
        assert result.intent == Intent.SERVER_IP
