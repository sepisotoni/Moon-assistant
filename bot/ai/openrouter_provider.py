from __future__ import annotations

import asyncio
import logging
import random

import httpx

from bot.ai.base import AIMessage, AIProvider, AIProviderError, AIResponse

logger = logging.getLogger(__name__)

# Adapted from openclaw's DISCORD_RETRY_DEFAULTS pattern (retry.ts)
_MAX_ATTEMPTS = 3
_MIN_DELAY_MS = 500
_MAX_DELAY_MS = 30_000
_JITTER = 0.1
_RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}


async def _backoff_delay(attempt: int, retry_after_ms: int | None = None) -> None:
    """Exponential backoff with jitter, honouring Retry-After when present."""
    if retry_after_ms is not None:
        delay_ms = retry_after_ms
    else:
        delay_ms = min(_MIN_DELAY_MS * (2 ** attempt), _MAX_DELAY_MS)
    jitter_ms = delay_ms * _JITTER * (2 * random.random() - 1)
    await asyncio.sleep((delay_ms + jitter_ms) / 1000)


class OpenRouterProvider(AIProvider):
    """Primary AI provider — OpenRouter's OpenAI-compatible chat completions API.

    Includes retry logic with exponential backoff + jitter, adapted from openclaw's
    rest-scheduler.ts (attempts=3, 500ms→30s, jitter=10%, honours Retry-After on 429).
    """

    name = "openrouter"

    def __init__(self, api_key: str, model: str, base_url: str = "https://openrouter.ai/api/v1") -> None:
        self._model = model
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/your-org/discord-ai-bot",
                "X-Title": "Discord AI Moderation Bot",
            },
            timeout=30.0,
        )

    async def generate(
        self,
        messages: list[AIMessage],
        *,
        temperature: float = 0.4,
        max_tokens: int = 800,
    ) -> AIResponse:
        payload = {
            "model": self._model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        last_exc: Exception | None = None
        for attempt in range(_MAX_ATTEMPTS):
            try:
                resp = await self._client.post("/chat/completions", json=payload)
                if resp.status_code in _RETRYABLE_STATUS and attempt < _MAX_ATTEMPTS - 1:
                    retry_after_ms = None
                    if resp.status_code == 429:
                        try:
                            retry_after_ms = int(float(resp.headers.get("retry-after", 0)) * 1000) or None
                        except (ValueError, TypeError):
                            pass
                        logger.warning("OpenRouter 429 rate-limited on model %s (attempt %d/%d)", self._model, attempt + 1, _MAX_ATTEMPTS)
                    await _backoff_delay(attempt, retry_after_ms)
                    continue
                resp.raise_for_status()
                data = resp.json()
                text = data["choices"][0]["message"]["content"]
                return AIResponse(text=text, provider=self.name, model=self._model, raw=data)
            except (httpx.HTTPError, KeyError, IndexError, TypeError) as exc:
                last_exc = exc
                if attempt < _MAX_ATTEMPTS - 1:
                    logger.warning("OpenRouter attempt %d/%d failed: %s — retrying", attempt + 1, _MAX_ATTEMPTS, exc)
                    await _backoff_delay(attempt)
                    continue
                break
        raise AIProviderError(f"OpenRouter request failed after {_MAX_ATTEMPTS} attempts: {last_exc}") from last_exc

    async def close(self) -> None:
        await self._client.aclose()
