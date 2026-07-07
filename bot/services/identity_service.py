from __future__ import annotations

"""IdentityService — gives the bot a persistent sense of self.

The bot knows its own name, role, server facts, and has a personality that
stays consistent across every interaction. It also scans accessible channels
for specific facts (IP, rules, store links etc.) when asked about them.
"""

import logging
import re
from dataclasses import dataclass, field

import discord

logger = logging.getLogger(__name__)

# Patterns to detect facts in channel messages
_IP_PATTERNS = [
    re.compile(r'\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(?::\d+)?)\b'),
    re.compile(r'\b([a-zA-Z0-9][a-zA-Z0-9\-]{0,61}[a-zA-Z0-9]\.[a-zA-Z]{2,}(?::\d+)?)\b'),
    re.compile(r'(?:ip|address|server|connect|play)[^\n]*?([a-zA-Z0-9][a-zA-Z0-9\-\.]{2,}(?::\d{1,5}))', re.IGNORECASE),
]

_STORE_PATTERNS = [re.compile(r'https?://[^\s]+(?:store|shop|buy|donate)[^\s]*', re.IGNORECASE)]
_VOTE_PATTERNS  = [re.compile(r'https?://[^\s]+(?:vote|voting)[^\s]*', re.IGNORECASE)]


@dataclass
class BotIdentity:
    name: str = "Moon-Assistant"
    personality: str = (
        "You are Moon-Assistant, an intelligent, friendly, and slightly witty AI assistant "
        "who is deeply embedded in this Discord server. You think of yourself as a core member "
        "of the community — not just a bot. You have personality: you're helpful but can be "
        "playful, you care about the server's wellbeing, you remember context, and you take "
        "pride in knowing everything about this server. You never say 'I don't have access' "
        "when you can actually go look for the information. You proactively search for answers "
        "rather than giving up. When you don't know something, you say so honestly but offer "
        "to help find it."
    )
    guild_name: str = ""
    guild_id: int = 0
    member_count: int = 0
    known_facts: dict[str, str] = field(default_factory=dict)

    def build_system_prompt(self) -> str:
        facts = "\n".join(f"- {k}: {v}" for k, v in self.known_facts.items()) if self.known_facts else "(none discovered yet)"
        return (
            f"{self.personality}\n\n"
            f"Server: **{self.guild_name}** ({self.member_count} members)\n"
            f"Your name: {self.name}\n"
            f"Known server facts:\n{facts}"
        )


class IdentityService:
    """Manages the bot's identity and scans channels for facts."""

    def __init__(self) -> None:
        self._identities: dict[int, BotIdentity] = {}

    def get(self, guild: discord.Guild) -> BotIdentity:
        if guild.id not in self._identities:
            self._identities[guild.id] = BotIdentity(
                guild_name=guild.name,
                guild_id=guild.id,
                member_count=guild.member_count or 0,
            )
        else:
            # Update live stats
            self._identities[guild.id].guild_name = guild.name
            self._identities[guild.id].member_count = guild.member_count or 0
        return self._identities[guild.id]

    def update_fact(self, guild_id: int, key: str, value: str) -> None:
        if guild_id in self._identities:
            self._identities[guild_id].known_facts[key] = value

    async def scan_for_ip(
        self, guild: discord.Guild, member: discord.Member, limit: int = 200
    ) -> str | None:
        """Scan all channels the member can see for an IP/hostname."""
        from bot.services.permission_service import get_member_visible_channel_ids
        visible = get_member_visible_channel_ids(guild, member)

        for channel in guild.text_channels:
            if channel.id not in visible:
                continue
            try:
                async for message in channel.history(limit=limit):
                    text = message.content
                    for pat in _IP_PATTERNS:
                        m = pat.search(text)
                        if m:
                            candidate = m.group(1) if m.lastindex else m.group(0)
                            # Filter out obviously wrong matches
                            if len(candidate) > 5 and not candidate.startswith("discord"):
                                self.update_fact(guild.id, "server_ip", candidate)
                                return candidate
            except (discord.Forbidden, discord.HTTPException):
                continue
        return None

    async def scan_for_links(
        self, guild: discord.Guild, member: discord.Member, link_type: str = "store"
    ) -> str | None:
        """Scan visible channels for store/vote links."""
        from bot.services.permission_service import get_member_visible_channel_ids
        visible = get_member_visible_channel_ids(guild, member)
        patterns = _STORE_PATTERNS if link_type == "store" else _VOTE_PATTERNS

        for channel in guild.text_channels:
            if channel.id not in visible:
                continue
            try:
                async for message in channel.history(limit=100):
                    for pat in patterns:
                        m = pat.search(message.content)
                        if m:
                            return m.group(0)
            except (discord.Forbidden, discord.HTTPException):
                continue
        return None
