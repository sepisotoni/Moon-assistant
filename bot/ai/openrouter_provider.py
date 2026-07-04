from __future__ import annotations

import httpx

from bot.ai.base import AIMessage, AIProvider, AIProviderError, AIResponse


class OpenRouterProvider(AIProvider):
    """Primary AI provider, using OpenRouter's OpenAI-compatible chat completions API."""

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
        try:
            resp = await self._client.post("/chat/completions", json=payload)
            resp.raise_for_status()
            data = resp.json()
            text = data["choices"][0]["message"]["content"]
            return AIResponse(text=text, provider=self.name, model=self._model, raw=data)
        except (httpx.HTTPError, KeyError, IndexError, TypeError) as exc:
            raise AIProviderError(f"OpenRouter request failed: {exc}") from exc

    async def close(self) -> None:
        await self._client.aclose()
