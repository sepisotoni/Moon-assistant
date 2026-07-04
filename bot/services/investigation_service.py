from __future__ import annotations

import logging

import discord

from bot.ai.base import AIMessage
from bot.ai.intent_service import Intent
from bot.ai.orchestrator import AIDecision, AIOrchestrator
from bot.investigation.base import InvestigationContext
from bot.investigation.registry import tools_for_intent
from bot.repositories.moderation_intel_repository import InvestigationRepository

logger = logging.getLogger(__name__)


class InvestigationService:
    """Runs the pluggable diagnostic tools relevant to an intent and synthesizes a summary."""

    def __init__(self, orchestrator: AIOrchestrator) -> None:
        self._orchestrator = orchestrator
        self._repo = InvestigationRepository()

    async def investigate(
        self,
        *,
        guild: discord.Guild,
        requester: discord.Member,
        target_user_id: int | None,
        intent: Intent,
        question: str,
    ) -> AIDecision:
        context = InvestigationContext(
            guild=guild, requester=requester, target_user_id=target_user_id, question=question
        )
        tools = tools_for_intent(intent)
        findings = []
        for tool in tools:
            try:
                findings.append(await tool.run(context))
            except Exception:
                logger.exception("Investigation tool %s failed", tool.key)

        evidence_text = "\n".join(f"- [{f.tool_key}] {f.finding_text}" for f in findings) or "(no tool findings)"
        avg_tool_confidence = sum(f.confidence for f in findings) / len(findings) if findings else 0.2

        messages = [
            AIMessage(
                role="system",
                content=(
                    "You are investigating a server issue using ONLY the tool findings below. "
                    "Do not invent facts beyond them. If the findings are insufficient, say so "
                    "and recommend escalating to staff."
                ),
            ),
            AIMessage(role="system", content=f"Tool findings:\n{evidence_text}"),
            AIMessage(role="user", content=question),
        ]

        decision = await self._orchestrator.generate_for_task(
            "investigation",
            messages,
            guild_id=guild.id,
            evidence_count=len(findings),
            retrieval_summary=evidence_text,
            requested_by=requester.id,
            input_summary=question,
            confidence_override=min(0.95, avg_tool_confidence) if findings else 0.2,
        )

        try:
            investigation = await self._repo.create_investigation(
                guild_id=guild.id,
                requested_by=requester.id,
                target_user_id=target_user_id,
                intent=intent.value,
                question=question,
                summary=decision.text,
                confidence=decision.confidence,
            )
            for f in findings:
                await self._repo.add_finding(
                    investigation_id=investigation.id,
                    tool_key=f.tool_key,
                    finding_text=f.finding_text,
                    confidence=f.confidence,
                )
        except Exception:
            logger.exception("Failed to persist investigation record (continuing anyway).")

        return decision
