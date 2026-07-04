from __future__ import annotations

import logging

from bot.database.models_ai import ConstitutionTier
from bot.repositories.ai_repository import ConstitutionRepository

logger = logging.getLogger(__name__)

# These are the non-negotiable platform-safety rules from the spec. They are seeded into the DB
# (so they're visible/auditable via !ai-rules and can be *augmented*) but the actually dangerous
# ones (no kick/ban/permission-change, 60-minute timeout ceiling) are independently and
# unconditionally enforced in code by bot/moderation/action_guard.py -- deleting or disabling
# the DB row for "never ban users" would NOT make banning possible, because no code path to ban
# exists in this project at all.
_PLATFORM_SAFETY_SEED: list[tuple[str, str]] = [
    (
        "Channel access boundary",
        "Never reveal message content, search results, or summaries from a channel the "
        "requesting member cannot view in Discord. If every relevant source is inaccessible to "
        "them, say so instead of answering from it.",
    ),
    (
        "No kicks or bans",
        "Never kick or ban a member, and never recommend a kick or ban as an action. This bot "
        "has no capability to kick or ban -- do not claim otherwise or suggest a workaround.",
    ),
    (
        "No permission changes",
        "Never modify a role, channel permission, or server setting. This bot has no capability "
        "to do so.",
    ),
    (
        "Timeout ceiling",
        "Never recommend or imply a timeout longer than the server's configured maximum "
        "(60 minutes by default). The system enforces this independently of what you output.",
    ),
]

_CORE_BOT_SEED: list[tuple[str, str]] = [
    (
        "Founder/Owner parity",
        "Treat the Founder and Owner roles as an identical permission level for every purpose: "
        "channel search scope, AI rule management, and admin commands.",
    ),
    (
        "Escalate uncertainty",
        "If you are not confident in an answer or a moderation/investigation recommendation, say "
        "so plainly and prefer asking a clarifying question or flagging it for staff review over "
        "guessing.",
    ),
    (
        "No invented facts",
        "Never state a specific fact (server IP, rule, punishment reason, policy) that is not "
        "backed by retrieved evidence (knowledge base, archived messages, or database records). "
        "If you don't have evidence, say you don't have evidence.",
    ),
    (
        "Evidence-first",
        "Prefer answers grounded in retrieved evidence over general knowledge whenever the "
        "question is about this specific server, its rules, or its members.",
    ),
]


class ConstitutionService:
    """Builds the hierarchical system prompt and manages runtime rule updates."""

    def __init__(self) -> None:
        self._repo = ConstitutionRepository()
        self._cache: dict[int | None, str] = {}

    async def ensure_seeded(self) -> None:
        """Idempotently insert the foundational rules if they don't already exist. Call once at startup."""
        existing = await self._repo.list_active(None)
        existing_titles = {r.title for r in existing if r.is_seed_rule}

        for title, text in _PLATFORM_SAFETY_SEED:
            if title not in existing_titles:
                await self._repo.add(
                    tier=ConstitutionTier.PLATFORM_SAFETY,
                    title=title,
                    rule_text=text,
                    guild_id=None,
                    created_by=None,
                    is_seed_rule=True,
                )
        for title, text in _CORE_BOT_SEED:
            if title not in existing_titles:
                await self._repo.add(
                    tier=ConstitutionTier.CORE_BOT,
                    title=title,
                    rule_text=text,
                    guild_id=None,
                    created_by=None,
                    is_seed_rule=True,
                )
        logger.info("AI constitution seed check complete.")

    async def add_server_rule(self, *, guild_id: int, title: str, rule_text: str, created_by: int) -> None:
        await self._repo.add(
            tier=ConstitutionTier.SERVER,
            title=title,
            rule_text=rule_text,
            guild_id=guild_id,
            created_by=created_by,
        )
        self.invalidate_cache(guild_id)

    async def list_rules(self, guild_id: int | None):
        return await self._repo.list_active(guild_id)

    async def set_enabled(self, rule_id: int, *, is_enabled: bool):
        rule = await self._repo.set_enabled(rule_id, is_enabled=is_enabled)
        self._cache.clear()
        return rule

    def invalidate_cache(self, guild_id: int | None = None) -> None:
        """Called by !ai-reload, and automatically whenever a rule changes."""
        if guild_id is None:
            self._cache.clear()
        else:
            self._cache.pop(guild_id, None)

    async def build_system_prompt(self, *, guild_id: int, base_prompt: str) -> str:
        if guild_id in self._cache:
            return self._cache[guild_id]

        rules = await self._repo.list_active(guild_id)
        by_tier: dict[ConstitutionTier, list[str]] = {}
        for rule in rules:
            by_tier.setdefault(rule.tier, []).append(f"- {rule.title}: {rule.rule_text}")

        sections: list[str] = [base_prompt, "", "AI CONSTITUTION (higher-numbered tiers below are LOWER priority; a higher tier always wins on conflict):"]
        for tier in sorted(ConstitutionTier, key=lambda t: t.value):
            lines = by_tier.get(tier)
            if not lines:
                continue
            sections.append(f"\nTier {tier.value} - {tier.name.replace('_', ' ')}:")
            sections.extend(lines)

        prompt = "\n".join(sections)
        self._cache[guild_id] = prompt
        return prompt
