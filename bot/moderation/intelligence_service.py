from __future__ import annotations

import datetime as dt
import json
import logging
import re

import discord
from sqlalchemy import select

from bot.ai.base import AIMessage
from bot.ai.orchestrator import AIDecision, AIOrchestrator
from bot.config import get_settings
from bot.database.models import Message, ModerationAction
from bot.database.models_moderation_intel import RecommendedAction, ReportStatus
from bot.database.session import get_session
from bot.moderation.action_guard import assert_action_allowed
from bot.moderation.service import ModerationService
from bot.repositories.moderation_intel_repository import ModerationIntelRepository

logger = logging.getLogger(__name__)
settings = get_settings()

# ---------------------------------------------------------------------------
# Real-time toxicity / harassment heuristic (fires before any AI call)
# Add/remove patterns here; these match obvious violations without an API call.
# ---------------------------------------------------------------------------
_TOXICITY_PATTERNS: list[re.Pattern] = [
    re.compile(r"\bkill\s+your?self\b", re.IGNORECASE),
    re.compile(r"\bkys\b", re.IGNORECASE),
    re.compile(r"\b(go\s+die|hope\s+you\s+die)\b", re.IGNORECASE),
]
_HARASSMENT_PATTERNS: list[re.Pattern] = [
    re.compile(r"\b(i('?ll)?\s+find\s+you|i\s+know\s+where\s+you\s+live)\b", re.IGNORECASE),
    re.compile(r"\b(dox(x?ing)?|dox\s+you)\b", re.IGNORECASE),
]


def detect_toxicity_heuristic(content: str) -> str | None:
    """Return a short description if obvious toxicity/harassment is found, else None."""
    for pat in _TOXICITY_PATTERNS:
        if pat.search(content):
            return "Potential toxic/self-harm language detected by heuristic filter."
    for pat in _HARASSMENT_PATTERNS:
        if pat.search(content):
            return "Potential harassment/doxxing language detected by heuristic filter."
    return None


def _moderation_agreement(text_a: str, text_b: str) -> bool:
    """Agreement check on recommended_action field — phrasing differences don't matter."""
    try:
        a = json.loads(text_a).get("recommended_action")
        b = json.loads(text_b).get("recommended_action")
        return a is not None and a == b
    except (json.JSONDecodeError, AttributeError):
        return False


class ModerationIntelligenceService:
    """AI-assisted report analysis.

    Fixes applied vs. the audit:
      - Real-time toxicity/harassment heuristic pre-filter.
      - Surrounding channel-context message window in evidence.
      - Repeated-offender auto-report trigger (also exposed as _static_repeat_check
        so ModerationService.warn() can call it without a circular import).
      - Auto-delete actually fetches and deletes the message via stored report IDs.
      - JSON code-fence stripping before parsing AI response.
    """

    def __init__(self, orchestrator: AIOrchestrator) -> None:
        self._orchestrator = orchestrator
        self._repo = ModerationIntelRepository()
        self._moderation_service = ModerationService()

    # ------------------------------------------------------------------
    # Evidence gathering
    # ------------------------------------------------------------------
    async def _gather_context(
        self,
        *,
        guild: discord.Guild,
        user_id: int,
        channel_id: int | None,
        reported_message_id: int | None,
    ) -> str:
        guild_id = guild.id
        lines: list[str] = []

        async with get_session() as session:
            # User's recent messages across the guild (last 20).
            recent_messages = list(
                (
                    await session.execute(
                        select(Message)
                        .where(Message.guild_id == guild_id, Message.author_id == user_id)
                        .order_by(Message.created_at.desc())
                        .limit(20)
                    )
                ).scalars().all()
            )

            # Punishment history in the last 24 h.
            since_24h = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=24)
            punishment_history = list(
                (
                    await session.execute(
                        select(ModerationAction).where(
                            ModerationAction.guild_id == guild_id,
                            ModerationAction.user_id == user_id,
                            ModerationAction.created_at >= since_24h,
                        )
                    )
                ).scalars().all()
            )

            # Warning count in repeat-offender lookback window.
            lookback = dt.datetime.now(dt.timezone.utc) - dt.timedelta(
                hours=settings.repeat_offender_lookback_hours
            )
            recent_warnings = list(
                (
                    await session.execute(
                        select(ModerationAction).where(
                            ModerationAction.guild_id == guild_id,
                            ModerationAction.user_id == user_id,
                            ModerationAction.action_type == "warn",
                            ModerationAction.created_at >= lookback,
                        )
                    )
                ).scalars().all()
            )

            # Surrounding channel-context window (±10 messages).
            surrounding_lines: list[str] = []
            if channel_id is not None:
                surrounding = list(
                    (
                        await session.execute(
                            select(Message)
                            .where(
                                Message.guild_id == guild_id,
                                Message.channel_id == channel_id,
                                Message.is_deleted.is_(False),
                            )
                            .order_by(Message.created_at.desc())
                            .limit(10)
                        )
                    ).scalars().all()
                )
                surrounding.sort(key=lambda m: m.created_at)
                surrounding_lines = [f"  {m.author_name}: {m.content[:150]}" for m in surrounding]

        report_count = await self._repo.count_recent_reports(
            guild_id, user_id, since=dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=24)
        )
        is_repeat = len(recent_warnings) >= settings.repeat_offender_warning_count

        lines.append(f"=== Reported user (id={user_id}) ===")
        lines.append(f"Recent messages (last 20 across guild):")
        lines.extend(f"  - {m.content[:200]}" for m in recent_messages[:10])
        lines.append(f"Punishment actions last 24h: {len(punishment_history)}")
        lines.append(f"Reports against user last 24h: {report_count}")
        lines.append(
            f"Warnings last {settings.repeat_offender_lookback_hours}h: {len(recent_warnings)}"
            + (" ⚠️ REPEAT OFFENDER" if is_repeat else "")
        )
        if surrounding_lines:
            lines.append(f"\n=== Channel context (last 10 messages) ===")
            lines.extend(surrounding_lines)

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Analyze a report
    # ------------------------------------------------------------------
    async def analyze_report(
        self,
        *,
        guild: discord.Guild,
        reported_user_id: int,
        reporter_id: int | None,
        channel_id: int | None,
        reported_message_id: int | None,
        reason: str,
        source: str = "user",
    ) -> tuple[AIDecision, dict]:
        # Heuristic pre-filter.
        heuristic = detect_toxicity_heuristic(reason)
        if heuristic:
            logger.info("Toxicity heuristic triggered for user %s: %s", reported_user_id, heuristic)
            source = "heuristic:toxicity"

        report = await self._repo.create_report(
            guild_id=guild.id,
            reported_user_id=reported_user_id,
            reporter_id=reporter_id,
            channel_id=channel_id,
            reported_message_id=reported_message_id,
            reason=reason,
            source=source,
        )

        evidence = await self._gather_context(
            guild=guild,
            user_id=reported_user_id,
            channel_id=channel_id,
            reported_message_id=reported_message_id,
        )

        system_prompt = (
            "You are a moderation analysis assistant. Allowed recommended_action values: "
            "none, warn, delete_message, timeout, escalate. "
            "NEVER recommend kick or ban. "
            "Respond ONLY with valid JSON, no markdown fences:\n"
            '{"risk_score":<0-1>,"confidence":<0-1>,'
            '"recommended_action":"<value>","evidence_summary":"<text>",'
            '"timeout_minutes":<1-60 or null>}'
        )
        messages = [
            AIMessage(role="system", content=system_prompt),
            AIMessage(role="system", content=f"Report reason: {reason}\n\nEvidence:\n{evidence}"),
            AIMessage(role="user", content="Analyze this report and recommend an action."),
        ]

        decision = await self._orchestrator.generate_for_task(
            "moderation_review",
            messages,
            guild_id=guild.id,
            dual_review=True,
            evidence_count=evidence.count("\n  - ") + 1,
            retrieval_summary=evidence,
            input_summary=reason,
            agreement_fn=_moderation_agreement,
        )

        parsed = self._safe_parse(decision.text)

        await self._repo.save_analysis(
            report_id=report.id,
            risk_score=float(parsed.get("risk_score", 0.5)),
            confidence=decision.confidence,
            recommended_action=RecommendedAction(parsed.get("recommended_action", "escalate")),
            evidence_summary=parsed.get("evidence_summary", evidence[:1000]),
            primary_model=f"{decision.provider}/{decision.model}",
            secondary_model=(
                f"{decision.secondary_provider}/{decision.secondary_model}"
                if decision.secondary_model else None
            ),
            agreement=decision.agreement,
            action_taken=False,
        )

        if decision.escalate or not decision.agreement:
            await self._repo.set_report_status(report.id, ReportStatus.ESCALATED)
            await self._repo.create_escalation(
                guild_id=guild.id,
                source="moderation",
                summary=(
                    f"Report on <@{reported_user_id}> needs staff review: "
                    f"{parsed.get('evidence_summary', reason)}"
                ),
                confidence=decision.confidence,
                related_report_id=report.id,
            )
        else:
            await self._repo.set_report_status(report.id, ReportStatus.PENDING)

        parsed["report_id"] = report.id
        return decision, parsed

    def _safe_parse(self, text: str) -> dict:
        cleaned = re.sub(r"```(?:json)?|```", "", text).strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            logger.warning("Could not parse moderation JSON: %r", text)
            return {
                "risk_score": 0.5,
                "confidence": 0.3,
                "recommended_action": "escalate",
                "evidence_summary": text[:500],
                "timeout_minutes": None,
            }

    # ------------------------------------------------------------------
    # Apply automated action
    # ------------------------------------------------------------------
    async def maybe_auto_apply(
        self,
        *,
        guild: discord.Guild,
        moderator: discord.Member,
        target: discord.Member,
        decision: AIDecision,
        parsed: dict,
        timeout_minutes: int | None = None,
    ) -> str:
        action = parsed.get("recommended_action", "escalate")
        assert_action_allowed(action)

        if action in ("none", "escalate"):
            return "No automated action was taken; requires staff judgement."
        if decision.escalate or decision.agreement is False:
            return "Dual review did not agree (or confidence too low); escalated to staff."
        if decision.confidence < settings.auto_action_confidence_threshold:
            return "Confidence below threshold; escalated to staff."

        report_id = parsed.get("report_id")

        if action == "warn":
            await self._moderation_service.warn(
                guild=guild,
                member=target,
                moderator=moderator,
                reason="AI-assisted: " + parsed.get("evidence_summary", ""),
                check_repeat_offender=False,  # avoid double-trigger
            )
            result = f"✅ Automatically warned {target.mention} (confidence {decision.confidence:.0%})."

        elif action == "timeout":
            ai_min = parsed.get("timeout_minutes")
            minutes = int(ai_min) if isinstance(ai_min, (int, float)) and ai_min > 0 else (timeout_minutes or 10)
            await self._moderation_service.timeout(
                member=target,
                moderator=moderator,
                minutes=minutes,
                reason="AI-assisted: " + parsed.get("evidence_summary", ""),
            )
            result = f"✅ Timed out {target.mention} for {minutes}m (confidence {decision.confidence:.0%})."

        elif action == "delete_message":
            result = await self._auto_delete_message(guild=guild, moderator=moderator, parsed=parsed)

        else:
            result = "No automated action was taken."

        if report_id is not None:
            await self._repo.set_report_status(report_id, ReportStatus.AUTO_RESOLVED)
        return result

    async def _auto_delete_message(
        self, *, guild: discord.Guild, moderator: discord.Member, parsed: dict
    ) -> str:
        """Fetch and delete the reported message using IDs stored on the report row."""
        report_id = parsed.get("report_id")
        if report_id is None:
            return "Could not auto-delete: no report ID."

        async with get_session() as session:
            from bot.database.models_moderation_intel import ModerationReport
            report = await session.get(ModerationReport, report_id)

        if not report or not report.channel_id or not report.reported_message_id:
            return "Could not auto-delete: report does not reference a specific message."

        channel = guild.get_channel(report.channel_id)
        if not isinstance(channel, discord.TextChannel):
            return "Could not auto-delete: channel not accessible."

        try:
            msg = await channel.fetch_message(report.reported_message_id)
            await msg.delete()
            async with get_session() as session:
                from bot.database.models import ModerationActionType
                session.add(ModerationAction(
                    guild_id=guild.id,
                    user_id=report.reported_user_id,
                    moderator_id=moderator.id,
                    action_type=ModerationActionType.DELETE_MESSAGE,
                    reason="AI-assisted auto-delete: " + parsed.get("evidence_summary", ""),
                ))
            return f"✅ Auto-deleted the reported message in {channel.mention}."
        except discord.NotFound:
            return "Message was already deleted."
        except discord.Forbidden:
            return "❌ Missing permissions to delete — please delete manually."

    # ------------------------------------------------------------------
    # Repeated-offender check
    # ------------------------------------------------------------------
    async def check_repeat_offender(self, *, guild: discord.Guild, user_id: int) -> bool:
        return await self._static_repeat_check(guild.id, user_id)

    @staticmethod
    async def _static_repeat_check(guild_id: int, user_id: int) -> bool:
        """DB-only repeat-offender check, safe to call without an orchestrator reference.

        Called by ModerationService.warn() via a deferred import to avoid circular imports.
        """
        repo = ModerationIntelRepository()
        since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(
            hours=settings.repeat_offender_lookback_hours
        )
        async with get_session() as session:
            warnings = list(
                (
                    await session.execute(
                        select(ModerationAction).where(
                            ModerationAction.guild_id == guild_id,
                            ModerationAction.user_id == user_id,
                            ModerationAction.action_type == "warn",
                            ModerationAction.created_at >= since,
                        )
                    )
                ).scalars().all()
            )

        if len(warnings) < settings.repeat_offender_warning_count:
            return False

        logger.warning(
            "Repeat offender: user %s in guild %s has %d warnings in %dh — auto-report submitted.",
            user_id, guild_id, len(warnings), settings.repeat_offender_lookback_hours,
        )
        await repo.create_report(
            guild_id=guild_id,
            reported_user_id=user_id,
            reporter_id=None,
            channel_id=None,
            reported_message_id=None,
            reason=f"Repeat offender: {len(warnings)} warnings in {settings.repeat_offender_lookback_hours}h",
            source="heuristic:repeat_offender",
        )
        return True
