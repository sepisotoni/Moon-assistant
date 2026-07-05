from __future__ import annotations

import logging

import discord

from bot.ai.base import AIMessage
from bot.ai.constitution_service import ConstitutionService
from bot.ai.intent_service import Intent, IntentDetectionService
from bot.ai.orchestrator import AIDecision, AIOrchestrator
from bot.config import get_settings
from bot.knowledge.constants import KNOWLEDGE_CHANNEL_NAMES
from bot.knowledge.retriever import KnowledgeRetriever
from bot.services.memory_service import MemoryService
from bot.services.search_service import SearchService

logger = logging.getLogger(__name__)
settings = get_settings()


class SupportEngine:
    """Answers support questions by combining knowledge retrieval, permission-aware message
    retrieval, announcement retrieval, and AI reasoning -- the spec's "support engine".

    This sits above (and reuses) Phase 1's SearchService and KnowledgeRetriever rather than
    duplicating their permission-aware logic.
    """

    def __init__(
        self,
        orchestrator: AIOrchestrator,
        constitution: ConstitutionService,
        intent_service: IntentDetectionService,
    ) -> None:
        self._orchestrator = orchestrator
        self._constitution = constitution
        self._intent_service = intent_service
        self._knowledge = KnowledgeRetriever()
        self._search = SearchService()
        self._memory = MemoryService()

    async def answer(
        self,
        *,
        guild: discord.Guild,
        member: discord.Member,
        channel_id: int,
        question: str,
        history: list | None = None,
    ) -> tuple[AIDecision, Intent]:
        intent_result = await self._intent_service.detect(question)

        knowledge_hits = await self._knowledge.search(guild.id, question, limit=5)
        # Permission-aware: a member only ever sees archived-message context from channels
        # they can view themselves, exactly like /search.
        message_hits = await self._search.search(guild=guild, member=member, query=question, limit=5)

        evidence_lines: list[str] = []
        for k in knowledge_hits:
            tag = "announcement" if k.channel_name in KNOWLEDGE_CHANNEL_NAMES else "knowledge"
            evidence_lines.append(f"[{tag}:{k.channel_name}] {k.content}")
        for m in message_hits:
            evidence_lines.append(f"[message] {m.author_name}: {m.content}")

        recurring = await self._memory.get_recurring(guild_id=guild.id, topic_key=intent_result.intent.value)
        if recurring:
            evidence_lines.append(f"[past resolution for similar questions] {recurring}")

        evidence_text = "\n".join(evidence_lines) if evidence_lines else "(no relevant evidence retrieved)"

        base_prompt = await self._constitution.build_system_prompt(
            guild_id=guild.id, base_prompt=settings.ai_system_prompt
        )

        # Inject live server context so the bot knows basic facts about this guild.
        server_context = (
            f"You are the AI assistant for Discord server: **{guild.name}** "
            f"(id={guild.id}, {guild.member_count} members). "
            f"Text channels: {', '.join(f'#{c.name}' for c in guild.text_channels[:15])}. "
            "Use this to answer questions about the server by name."
        )

        messages = [
            AIMessage(role="system", content=base_prompt),
            AIMessage(role="system", content=server_context),
            AIMessage(
                role="system",
                content=(
                    f"Detected user intent: {intent_result.intent.value} "
                    f"(confidence {intent_result.confidence:.2f}).\n"
                    f"Retrieved evidence:\n{evidence_text}"
                ),
            ),
        ]
        # Inject conversation history BEFORE the new user message so the AI
        # has full multi-turn context (resolves pronouns like "him", "that", "it").
        if history:
            messages.extend(history)
        messages.append(AIMessage(role="user", content=question))

        decision = await self._orchestrator.generate_for_task(
            "support",
            messages,
            guild_id=guild.id,
            dual_review=False,
            evidence_count=len(evidence_lines),
            retrieval_summary=evidence_text,
            requested_by=member.id,
            input_summary=question,
        )

        if not decision.escalate:
            await self._memory.record_recurring(
                guild_id=guild.id, topic_key=intent_result.intent.value, resolution=decision.text
            )
        await self._memory.remember_conversation_turn(
            guild_id=guild.id, channel_id=channel_id, user_id=member.id, summary=f"Q: {question}\nA: {decision.text}"
        )

        return decision, intent_result.intent
