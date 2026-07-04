from __future__ import annotations

from sqlalchemy import select

from bot.ai.base import AIMessage
from bot.database.models import AIRule, AIRuleScope
from bot.database.session import get_session


class AIRulesService:
    """CRUD over database-stored AI rules, and composition into a system prompt."""

    async def list_rules(self, guild_id: int) -> list[AIRule]:
        async with get_session() as session:
            stmt = (
                select(AIRule)
                .where(
                    AIRule.is_enabled.is_(True),
                    (AIRule.scope == AIRuleScope.GLOBAL) | (AIRule.guild_id == guild_id),
                )
                .order_by(AIRule.priority.asc())
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def add_rule(
        self,
        *,
        name: str,
        prompt_text: str,
        created_by: int,
        guild_id: int | None = None,
        channel_id: int | None = None,
        scope: AIRuleScope = AIRuleScope.GUILD,
        priority: int = 100,
    ) -> AIRule:
        async with get_session() as session:
            rule = AIRule(
                name=name,
                prompt_text=prompt_text,
                scope=scope,
                guild_id=guild_id,
                channel_id=channel_id,
                priority=priority,
                created_by=created_by,
            )
            session.add(rule)
            await session.flush()
            await session.refresh(rule)
            return rule

    async def remove_rule(self, rule_id: int) -> bool:
        async with get_session() as session:
            rule = await session.get(AIRule, rule_id)
            if rule is None:
                return False
            await session.delete(rule)
            return True

    async def build_system_prompt(self, *, base_prompt: str, guild_id: int) -> str:
        rules = await self.list_rules(guild_id)
        if not rules:
            return base_prompt
        rule_lines = "\n".join(f"- {r.name}: {r.prompt_text}" for r in rules)
        return f"{base_prompt}\n\nCommunity rules you must follow:\n{rule_lines}"

    async def build_system_message(self, *, base_prompt: str, guild_id: int) -> AIMessage:
        text = await self.build_system_prompt(base_prompt=base_prompt, guild_id=guild_id)
        return AIMessage(role="system", content=text)
