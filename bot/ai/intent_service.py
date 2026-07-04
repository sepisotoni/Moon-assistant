from __future__ import annotations

import difflib
import enum
import logging
import re
from dataclasses import dataclass

from bot.ai.base import AIMessage

logger = logging.getLogger(__name__)


class Intent(str, enum.Enum):
    SERVER_IP = "server_ip"
    JOIN_ISSUE = "join_issue"
    WHITELIST_ISSUE = "whitelist_issue"
    PUNISHMENT_QUESTION = "punishment_question"
    ACCOUNT_LINKING = "account_linking"
    RULES_QUESTION = "rules_question"
    VOTING_QUESTION = "voting_question"
    STORE_QUESTION = "store_question"
    REPORT_PLAYER = "report_player"
    TRANSLATION = "translation"
    SUMMARIZATION = "summarization"
    EXPLANATION = "explanation"
    INVESTIGATION = "investigation"
    MODERATION_REVIEW = "moderation_review"
    GENERAL_QUESTION = "general_question"


@dataclass
class IntentResult:
    intent: Intent
    confidence: float
    source: str  # "keyword" | "ai" | "default"


# Keyword sets per intent. Kept intentionally simple (no ML dependency); misspelling tolerance
# comes from fuzzy token matching against these keywords, not from the keyword list itself being
# exhaustive.
_INTENT_KEYWORDS: dict[Intent, list[str]] = {
    Intent.SERVER_IP: ["ip", "address", "server ip", "connect", "play.", "join address"],
    Intent.JOIN_ISSUE: ["can't join", "cant join", "won't connect", "wont connect", "connection failed",
                         "timed out", "timeout error", "unable to join", "kicked on join"],
    Intent.WHITELIST_ISSUE: ["whitelist", "whitelisted", "white list", "not whitelisted"],
    Intent.PUNISHMENT_QUESTION: ["banned", "ban appeal", "why was i banned", "muted", "timeout", "punished",
                                  "punishment", "warned", "warning"],
    Intent.ACCOUNT_LINKING: ["link my account", "link account", "linking", "discord link", "verify account"],
    Intent.RULES_QUESTION: ["rule", "rules", "allowed to", "is it allowed", "against the rules"],
    Intent.VOTING_QUESTION: ["vote", "voting", "vote link", "voting rewards"],
    Intent.STORE_QUESTION: ["store", "shop", "buy", "purchase", "donate", "donation", "price", "cost"],
    Intent.REPORT_PLAYER: ["report", "reporting", "report player", "report user"],
    Intent.TRANSLATION: ["translate", "translation", "what language", "in english", "in spanish"],
    Intent.SUMMARIZATION: ["summarize", "summarise", "summary", "tl;dr", "tldr"],
    Intent.EXPLANATION: ["explain", "what does he mean", "what does she mean", "what is going on",
                          "what's the argument", "what is the argument"],
    Intent.INVESTIGATION: ["investigate", "investigation", "look into", "find out why"],
    Intent.MODERATION_REVIEW: ["review this report", "moderation review", "is this toxic", "is this harassment"],
}

_FUZZY_CUTOFF = 0.8


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _keyword_score(text: str, keywords: list[str]) -> float:
    """Exact substring match scores 1.0; a fuzzy single-token match scores 0.6."""
    tokens = text.split()
    best = 0.0
    for kw in keywords:
        if kw in text:
            return 1.0
        for token in tokens:
            ratio = difflib.SequenceMatcher(None, token, kw).ratio()
            if ratio >= _FUZZY_CUTOFF:
                best = max(best, 0.6)
    return best


class IntentDetectionService:
    """Classifies free text into one of the supported Intents.

    Stage 1 (cheap, no AI call): keyword + fuzzy-token matching, tolerant of misspellings.
    Stage 2 (only if stage 1 is inconclusive): ask the AI orchestrator to classify, constrained
    to the same enum, returning its own confidence.
    """

    def __init__(self, orchestrator=None) -> None:
        # `orchestrator` is an AIOrchestrator (bot.ai.orchestrator). Optional so this service
        # can be unit-tested without any AI provider wired up.
        self._orchestrator = orchestrator

    def detect_keyword_only(self, text: str) -> IntentResult:
        normalized = _normalize(text)
        scores = {intent: _keyword_score(normalized, kws) for intent, kws in _INTENT_KEYWORDS.items()}
        best_intent, best_score = max(scores.items(), key=lambda kv: kv[1])
        if best_score <= 0:
            return IntentResult(intent=Intent.GENERAL_QUESTION, confidence=0.3, source="default")
        return IntentResult(intent=best_intent, confidence=best_score, source="keyword")

    async def detect(self, text: str, *, ai_fallback_threshold: float = 0.55) -> IntentResult:
        result = self.detect_keyword_only(text)
        if result.confidence >= ai_fallback_threshold or self._orchestrator is None:
            return result

        try:
            ai_result = await self._classify_with_ai(text)
            if ai_result is not None:
                return ai_result
        except Exception:
            logger.exception("AI intent fallback failed; using keyword result.")
        return result

    async def _classify_with_ai(self, text: str) -> IntentResult | None:
        valid = ", ".join(i.value for i in Intent)
        prompt = (
            "Classify the user's message into exactly one of these intents: "
            f"{valid}. Respond with strict JSON only: {{\"intent\": \"<one of the list>\", "
            '"confidence": <0.0-1.0>}. No other text.'
        )
        messages = [
            AIMessage(role="system", content=prompt),
            AIMessage(role="user", content=text),
        ]
        decision = await self._orchestrator.generate_for_task(
            "intent_classification", messages, dual_review=False
        )
        import json

        try:
            data = json.loads(decision.text.strip())
            intent = Intent(data["intent"])
            confidence = float(data["confidence"])
            return IntentResult(intent=intent, confidence=confidence, source="ai")
        except (ValueError, KeyError, TypeError):
            logger.warning("Could not parse AI intent classification response: %r", decision.text)
            return None
