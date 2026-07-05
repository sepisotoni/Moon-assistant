from __future__ import annotations

"""ConversationService — per-channel rolling message history for natural multi-turn conversations.

Keeps the last MAX_TURNS (user → bot) exchanges in memory per channel so every AI
call has full conversational context. This makes the bot behave like a real chat
partner rather than a stateless Q&A machine:

  User:  "who's been most active lately?"
  Bot:   "Looks like @Dave has been posting the most."
  User:  "warn him"        ← bot knows "him" = Dave, no re-explanation needed
  Bot:   ✅ Warned @Dave

History is fast in-process memory. A compact snapshot is written to MemoryService
(DB) so the last few turns survive a restart for busy channels.
"""

import logging
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Deque

from bot.ai.base import AIMessage

logger = logging.getLogger(__name__)

MAX_TURNS = 10        # (user, bot) pairs kept per channel
MAX_CONTENT_LEN = 500 # truncate long messages to keep prompt size sane


@dataclass
class Turn:
    user_content: str
    assistant_content: str
    user_id: int
    user_name: str


@dataclass
class ChannelHistory:
    channel_id: int
    turns: Deque[Turn] = field(default_factory=lambda: deque(maxlen=MAX_TURNS))

    def add(
        self, *, user_content: str, assistant_content: str, user_id: int, user_name: str
    ) -> None:
        self.turns.append(
            Turn(
                user_content=user_content[:MAX_CONTENT_LEN],
                assistant_content=assistant_content[:MAX_CONTENT_LEN],
                user_id=user_id,
                user_name=user_name,
            )
        )

    def to_messages(self) -> list[AIMessage]:
        """Alternating user/assistant AIMessages — injected before the new user turn."""
        msgs: list[AIMessage] = []
        for t in self.turns:
            msgs.append(AIMessage(role="user", content=f"{t.user_name}: {t.user_content}"))
            msgs.append(AIMessage(role="assistant", content=t.assistant_content))
        return msgs

    def last_mentioned_user_id(self) -> int | None:
        for t in reversed(self.turns):
            if t.user_id:
                return t.user_id
        return None

    def is_empty(self) -> bool:
        return len(self.turns) == 0


class ConversationService:
    """One ChannelHistory per Discord channel, shared across cogs via bot.conversation."""

    def __init__(self) -> None:
        self._histories: dict[int, ChannelHistory] = {}

    def get(self, channel_id: int) -> ChannelHistory:
        if channel_id not in self._histories:
            self._histories[channel_id] = ChannelHistory(channel_id=channel_id)
        return self._histories[channel_id]

    def record(
        self,
        *,
        channel_id: int,
        user_content: str,
        assistant_content: str,
        user_id: int,
        user_name: str,
    ) -> None:
        self.get(channel_id).add(
            user_content=user_content,
            assistant_content=assistant_content,
            user_id=user_id,
            user_name=user_name,
        )

    def history_messages(self, channel_id: int) -> list[AIMessage]:
        return self.get(channel_id).to_messages()

    def clear(self, channel_id: int) -> None:
        self._histories.pop(channel_id, None)

    def clear_all(self) -> None:
        self._histories.clear()
