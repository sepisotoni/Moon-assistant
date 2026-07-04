from __future__ import annotations

import httpx

from bot.ai.base import AIMessage, AIProvider, AIProviderError, AIResponse


class GeminiProvider(AIProvider):
    """Fallback AI provider, calling Google's Generative Language REST API directly."""

    name = "gemini"

    def __init__(self, api_key: str, model: str) -> None:
        self._api_key = api_key
        self._model = model
        self._client = httpx.AsyncClient(
            base_url="https://generativelanguage.googleapis.com/v1beta",
            timeout=30.0,
        )

    @staticmethod
    def _to_gemini_contents(messages: list[AIMessage]) -> tuple[str | None, list[dict]]:
        """Gemini has no 'system' role; system messages become a separate systemInstruction."""
        system_prompt: str | None = None
        contents: list[dict] = []
        for m in messages:
            if m.role == "system":
                system_prompt = f"{system_prompt}\n{m.content}" if system_prompt else m.content
                continue
            role = "model" if m.role == "assistant" else "user"
            contents.append({"role": role, "parts": [{"text": m.content}]})
        return system_prompt, contents

    async def generate(
        self,
        messages: list[AIMessage],
        *,
        temperature: float = 0.4,
        max_tokens: int = 800,
    ) -> AIResponse:
        system_prompt, contents = self._to_gemini_contents(messages)
        payload: dict = {
            "contents": contents,
            "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
        }
        if system_prompt:
            payload["systemInstruction"] = {"parts": [{"text": system_prompt}]}

        try:
            resp = await self._client.post(
                f"/models/{self._model}:generateContent",
                params={"key": self._api_key},
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            return AIResponse(text=text, provider=self.name, model=self._model, raw=data)
        except (httpx.HTTPError, KeyError, IndexError, TypeError) as exc:
            raise AIProviderError(f"Gemini request failed: {exc}") from exc

    async def close(self) -> None:
        await self._client.aclose()
