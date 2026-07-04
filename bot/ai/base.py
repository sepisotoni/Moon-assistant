from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Literal

Role = Literal["system", "user", "assistant"]


@dataclass
class AIMessage:
    role: Role
    content: str


@dataclass
class AIResponse:
    text: str
    provider: str
    model: str
    raw: dict = field(default_factory=dict)


class AIProviderError(RuntimeError):
    """Raised when a provider fails to produce a usable response."""


class AIProvider(ABC):
    """Common interface every AI backend (OpenRouter, Gemini, ...) must implement."""

    name: str = "base"

    @abstractmethod
    async def generate(
        self,
        messages: list[AIMessage],
        *,
        temperature: float = 0.4,
        max_tokens: int = 800,
    ) -> AIResponse:
        """Generate a single completion for the given conversation."""
        raise NotImplementedError

    async def close(self) -> None:
        """Release any underlying HTTP clients. Override if needed."""
        return None
