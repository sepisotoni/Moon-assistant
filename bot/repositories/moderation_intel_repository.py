from __future__ import annotations

import datetime as dt

from sqlalchemy import select

from bot.database.models_moderation_intel import (
    Investigation,
    InvestigationFinding,
    KnownIssue,
    LinkedAccount,
    ModerationAnalysis,
    ModerationReport,
    ReportStatus,
    StaffEscalation,
    WhitelistEntry,
)
from bot.database.session import get_session


class ModerationIntelRepository:
    """Data access for AI-assisted moderation: reports, analyses, escalations."""

    async def create_report(
        self,
        *,
        guild_id: int,
        reported_user_id: int,
        reporter_id: int | None,
        channel_id: int | None,
        reported_message_id: int | None,
        reason: str,
        source: str = "user",
    ) -> ModerationReport:
        async with get_session() as session:
            report = ModerationReport(
                guild_id=guild_id,
                reported_user_id=reported_user_id,
                reporter_id=reporter_id,
                channel_id=channel_id,
                reported_message_id=reported_message_id,
                reason=reason,
                source=source,
            )
            session.add(report)
            await session.flush()
            await session.refresh(report)
            return report

    async def set_report_status(self, report_id: int, status: ReportStatus) -> None:
        async with get_session() as session:
            report = await session.get(ModerationReport, report_id)
            if report is not None:
                report.status = status

    async def count_recent_reports(self, guild_id: int, user_id: int, *, since: dt.datetime) -> int:
        async with get_session() as session:
            stmt = select(ModerationReport).where(
                ModerationReport.guild_id == guild_id,
                ModerationReport.reported_user_id == user_id,
                ModerationReport.created_at >= since,
            )
            result = await session.execute(stmt)
            return len(list(result.scalars().all()))

    async def save_analysis(
        self,
        *,
        report_id: int,
        risk_score: float,
        confidence: float,
        recommended_action,
        evidence_summary: str,
        primary_model: str,
        secondary_model: str | None,
        agreement: bool | None,
        action_taken: bool,
    ) -> ModerationAnalysis:
        async with get_session() as session:
            analysis = ModerationAnalysis(
                report_id=report_id,
                risk_score=risk_score,
                confidence=confidence,
                recommended_action=recommended_action,
                evidence_summary=evidence_summary,
                primary_model=primary_model,
                secondary_model=secondary_model,
                agreement=agreement,
                action_taken=action_taken,
            )
            session.add(analysis)
            await session.flush()
            await session.refresh(analysis)
            return analysis

    async def create_escalation(
        self,
        *,
        guild_id: int,
        source: str,
        summary: str,
        confidence: float | None,
        related_report_id: int | None = None,
        related_investigation_id: int | None = None,
    ) -> StaffEscalation:
        async with get_session() as session:
            escalation = StaffEscalation(
                guild_id=guild_id,
                source=source,
                summary=summary,
                confidence=confidence,
                related_report_id=related_report_id,
                related_investigation_id=related_investigation_id,
            )
            session.add(escalation)
            await session.flush()
            await session.refresh(escalation)
            return escalation

    async def list_open_escalations(self, guild_id: int, limit: int = 20) -> list[StaffEscalation]:
        async with get_session() as session:
            stmt = (
                select(StaffEscalation)
                .where(StaffEscalation.guild_id == guild_id, StaffEscalation.resolved.is_(False))
                .order_by(StaffEscalation.created_at.desc())
                .limit(limit)
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())


class InvestigationRepository:
    """Data access for investigations and the reference data investigation tools consult."""

    async def create_investigation(
        self,
        *,
        guild_id: int,
        requested_by: int,
        target_user_id: int | None,
        intent: str,
        question: str,
        summary: str,
        confidence: float,
    ) -> Investigation:
        async with get_session() as session:
            inv = Investigation(
                guild_id=guild_id,
                requested_by=requested_by,
                target_user_id=target_user_id,
                intent=intent,
                question=question,
                summary=summary,
                confidence=confidence,
            )
            session.add(inv)
            await session.flush()
            await session.refresh(inv)
            return inv

    async def add_finding(
        self, *, investigation_id: int, tool_key: str, finding_text: str, confidence: float
    ) -> InvestigationFinding:
        async with get_session() as session:
            finding = InvestigationFinding(
                investigation_id=investigation_id,
                tool_key=tool_key,
                finding_text=finding_text,
                confidence=confidence,
            )
            session.add(finding)
            await session.flush()
            await session.refresh(finding)
            return finding

    async def recent(self, guild_id: int, limit: int = 10) -> list[Investigation]:
        async with get_session() as session:
            stmt = (
                select(Investigation)
                .where(Investigation.guild_id == guild_id)
                .order_by(Investigation.created_at.desc())
                .limit(limit)
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def known_issues(self, guild_id: int, *, only_open: bool = True) -> list[KnownIssue]:
        async with get_session() as session:
            stmt = select(KnownIssue).where(KnownIssue.guild_id == guild_id)
            if only_open:
                stmt = stmt.where(KnownIssue.is_resolved.is_(False))
            result = await session.execute(stmt.order_by(KnownIssue.created_at.desc()))
            return list(result.scalars().all())

    async def linked_account(self, guild_id: int, discord_user_id: int) -> LinkedAccount | None:
        async with get_session() as session:
            stmt = select(LinkedAccount).where(
                LinkedAccount.guild_id == guild_id, LinkedAccount.discord_user_id == discord_user_id
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def whitelist_entry(self, guild_id: int, *, ingame_username: str | None = None,
                               discord_user_id: int | None = None) -> WhitelistEntry | None:
        async with get_session() as session:
            stmt = select(WhitelistEntry).where(WhitelistEntry.guild_id == guild_id)
            if ingame_username is not None:
                stmt = stmt.where(WhitelistEntry.ingame_username == ingame_username)
            elif discord_user_id is not None:
                stmt = stmt.where(WhitelistEntry.discord_user_id == discord_user_id)
            else:
                return None
            result = await session.execute(stmt)
            return result.scalar_one_or_none()
