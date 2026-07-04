from __future__ import annotations

import logging

from bot.ai.base import AIMessage, AIProvider, AIProviderError, AIResponse

logger = logging.getLogger(__name__)


class AIProviderManager:
    """Tries the primary provider first (OpenRouter); falls back to a secondary (Gemini) on failure."""

    def __init__(self, primary: AIProvider, fallback: AIProvider | None = None) -> None:
        self._primary = primary
        self._fallback = fallback

    async def generate(self, messages: list[AIMessage], **kwargs) -> AIResponse:
        try:
            return await self._primary.generate(messages, **kwargs)
        except AIProviderError as exc:
            logger.warning("Primary AI provider (%s) failed: %s", self._primary.name, exc)
            if self._fallback is None:
                raise
            try:
                return await self._fallback.generate(messages, **kwargs)
            except AIProviderError as fallback_exc:
                logger.error("Fallback AI provider (%s) also failed: %s", self._fallback.name, fallback_exc)
                raise

    async def close(self) -> None:
        await self._primary.close()
        if self._fallback is not None:
            await self._fallback.close()
