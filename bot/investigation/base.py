from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import discord


@dataclass
class InvestigationContext:
    guild: discord.Guild
    requester: discord.Member
    target_user_id: int | None
    question: str


@dataclass
class ToolFinding:
    tool_key: str
    finding_text: str
    confidence: float


class InvestigationTool(ABC):
    """A single, pluggable diagnostic (whitelist status, punishment history, etc.).

    Tools must be side-effect free (read-only) and must respect permission boundaries: a tool
    that reads archived messages should use the same permission-aware filtering as /search,
    rather than a raw unrestricted query.
    """

    key: str = "base"

    @abstractmethod
    async def run(self, context: InvestigationContext) -> ToolFinding:
        raise NotImplementedError
