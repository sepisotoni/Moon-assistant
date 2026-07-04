from __future__ import annotations

from sqlalchemy import select

from bot.database.models_ai import AIDecisionLog, ConstitutionRule, ConstitutionTier
from bot.database.session import get_session


class ConstitutionRepository:
    """Data access for the hierarchical AI constitution."""

    async def list_active(self, guild_id: int | None) -> list[ConstitutionRule]:
        async with get_session() as session:
            stmt = (
                select(ConstitutionRule)
                .where(
                    ConstitutionRule.is_enabled.is_(True),
                    (ConstitutionRule.guild_id.is_(None)) | (ConstitutionRule.guild_id == guild_id),
                )
                .order_by(ConstitutionRule.tier.asc(), ConstitutionRule.priority.asc())
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def add(
        self,
        *,
        tier: ConstitutionTier,
        title: str,
        rule_text: str,
        guild_id: int | None,
        created_by: int | None,
        priority: int = 100,
        is_seed_rule: bool = False,
    ) -> ConstitutionRule:
        async with get_session() as session:
            rule = ConstitutionRule(
                tier=tier,
                title=title,
                rule_text=rule_text,
                guild_id=guild_id,
                priority=priority,
                created_by=created_by,
                is_seed_rule=is_seed_rule,
            )
            session.add(rule)
            await session.flush()
            await session.refresh(rule)
            return rule

    async def set_enabled(self, rule_id: int, *, is_enabled: bool) -> ConstitutionRule | None:
        async with get_session() as session:
            rule = await session.get(ConstitutionRule, rule_id)
            if rule is None:
                return None
            rule.is_enabled = is_enabled
            await session.flush()
            await session.refresh(rule)
            return rule


class DecisionLogRepository:
    """Data access for the AI decision audit trail (the confidence system's backbone)."""

    async def record(
        self,
        *,
        guild_id: int | None,
        task_type: str,
        requested_by: int | None,
        input_summary: str,
        output_summary: str,
        confidence: float,
        evidence_count: int,
        retrieval_summary: str | None,
        primary_provider: str | None,
        primary_model: str | None,
        secondary_provider: str | None = None,
        secondary_model: str | None = None,
        dual_review_agreement: bool | None = None,
        escalated: bool = False,
    ) -> AIDecisionLog:
        async with get_session() as session:
            entry = AIDecisionLog(
                guild_id=guild_id,
                task_type=task_type,
                requested_by=requested_by,
                input_summary=input_summary[:4000],
                output_summary=output_summary[:4000],
                confidence=confidence,
                evidence_count=evidence_count,
                retrieval_summary=retrieval_summary,
                primary_provider=primary_provider,
                primary_model=primary_model,
                secondary_provider=secondary_provider,
                secondary_model=secondary_model,
                dual_review_agreement=dual_review_agreement,
                escalated=escalated,
            )
            session.add(entry)
            await session.flush()
            await session.refresh(entry)
            return entry

    async def recent(self, guild_id: int | None, limit: int = 20) -> list[AIDecisionLog]:
        async with get_session() as session:
            stmt = select(AIDecisionLog).order_by(AIDecisionLog.created_at.desc()).limit(limit)
            if guild_id is not None:
                stmt = stmt.where(AIDecisionLog.guild_id == guild_id)
            result = await session.execute(stmt)
            return list(result.scalars().all())
