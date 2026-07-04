from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Callable

from bot.ai.base import AIMessage, AIProviderError
from bot.ai.manager import AIProviderManager
from bot.ai.model_routing import ModelRouter, NoHealthyModelError
from bot.config import Settings
from bot.repositories.ai_repository import DecisionLogRepository

logger = logging.getLogger(__name__)

_HEDGE_PATTERNS = re.compile(
    r"\b(not sure|don'?t know|cannot find|no information|unclear|uncertain|unable to confirm)\b",
    re.IGNORECASE,
)

AgreementFn = Callable[[str, str], bool]


@dataclass
class AIDecision:
    """The standard output wrapper for every AI-derived decision in the bot.

    Every place that surfaces an AI answer or recommendation should produce one of these so
    confidence, evidence, and escalation are handled consistently (the "confidence system").
    """

    text: str
    confidence: float
    evidence_count: int
    retrieval_summary: str | None
    provider: str
    model: str
    secondary_provider: str | None = None
    secondary_model: str | None = None
    secondary_text: str | None = None
    agreement: bool | None = None
    escalate: bool = False


def _default_agreement(text_a: str, text_b: str) -> bool:
    """Fallback agreement check: are the two answers roughly saying the same thing?

    This is intentionally crude (token-overlap based). Callers doing structured tasks (e.g.
    moderation analysis returning JSON) should pass their own `agreement_fn` that compares the
    parsed recommended_action field instead -- that's far more meaningful than text similarity.
    """
    import difflib

    return difflib.SequenceMatcher(None, text_a.strip().lower(), text_b.strip().lower()).ratio() >= 0.6


class AIOrchestrator:
    """Central entry point for every AI call in the bot.

    Wraps the new health-aware ModelRouter, falls back to the Phase 1 AIProviderManager if the
    router has no usable candidates (e.g. registry not seeded yet), supports single- or
    dual-model review, and always returns a confidence-annotated AIDecision that gets logged to
    ai_decision_logs.
    """

    def __init__(
        self,
        settings: Settings,
        router: ModelRouter,
        legacy_manager: AIProviderManager | None = None,
    ) -> None:
        self._settings = settings
        self._router = router
        self._legacy_manager = legacy_manager
        self._decision_log = DecisionLogRepository()

    def _estimate_confidence(self, text: str, evidence_count: int) -> float:
        base = 0.85 if evidence_count > 0 else 0.5
        if _HEDGE_PATTERNS.search(text):
            base *= 0.5
        if not text.strip():
            base = 0.05
        return max(0.05, min(0.99, base))

    async def _single_generate(self, *, guild_id: int, task_type: str, messages: list[AIMessage], exclude=None, **kwargs):
        try:
            response, config = await self._router.generate(
                guild_id=guild_id, task_type=task_type, messages=messages, exclude_model_ids=exclude, **kwargs
            )
            return response, config.id
        except (NoHealthyModelError, AIProviderError) as exc:
            if self._legacy_manager is None:
                raise
            logger.warning("Model router unavailable for task %s (%s); using legacy AI manager.", task_type, exc)
            response = await self._legacy_manager.generate(messages, **kwargs)
            return response, None

    async def generate_for_task(
        self,
        task_type: str,
        messages: list[AIMessage],
        *,
        guild_id: int = 0,
        dual_review: bool = False,
        evidence_count: int = 0,
        retrieval_summary: str | None = None,
        requested_by: int | None = None,
        input_summary: str | None = None,
        confidence_override: float | None = None,
        agreement_fn: AgreementFn | None = None,
    ) -> AIDecision:
        response, used_model_id = await self._single_generate(guild_id=guild_id, task_type=task_type, messages=messages)

        confidence = confidence_override if confidence_override is not None else self._estimate_confidence(
            response.text, evidence_count
        )
        decision = AIDecision(
            text=response.text,
            confidence=confidence,
            evidence_count=evidence_count,
            retrieval_summary=retrieval_summary,
            provider=response.provider,
            model=response.model,
        )

        if dual_review and self._settings.dual_review_enabled:
            exclude = {used_model_id} if used_model_id is not None else None
            try:
                response2, _ = await self._single_generate(
                    guild_id=guild_id, task_type=task_type, messages=messages, exclude=exclude
                )
                agree = (agreement_fn or _default_agreement)(decision.text, response2.text)
                decision.secondary_provider = response2.provider
                decision.secondary_model = response2.model
                decision.secondary_text = response2.text
                decision.agreement = agree
                if not agree:
                    decision.escalate = True
            except (NoHealthyModelError, AIProviderError) as exc:
                logger.warning("Dual review could not get a second opinion for task %s: %s", task_type, exc)
                # We promised the model would be cross-checked for moderation/investigation
                # tasks; if we can't actually do that, escalate to staff rather than silently
                # downgrading to single-model trust.
                decision.agreement = None
                decision.escalate = True

        if decision.confidence < self._settings.confidence_escalation_threshold:
            decision.escalate = True

        try:
            await self._decision_log.record(
                guild_id=guild_id or None,
                task_type=task_type,
                requested_by=requested_by,
                input_summary=input_summary or (messages[-1].content if messages else ""),
                output_summary=decision.text,
                confidence=decision.confidence,
                evidence_count=decision.evidence_count,
                retrieval_summary=decision.retrieval_summary,
                primary_provider=decision.provider,
                primary_model=decision.model,
                secondary_provider=decision.secondary_provider,
                secondary_model=decision.secondary_model,
                dual_review_agreement=decision.agreement,
                escalated=decision.escalate,
            )
        except Exception:
            logger.exception("Failed to persist AI decision log (continuing anyway).")

        return decision

    async def close(self) -> None:
        await self._router.close()
        if self._legacy_manager is not None:
            await self._legacy_manager.close()
