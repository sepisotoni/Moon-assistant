from __future__ import annotations

import logging

import discord

from bot.ai.base import AIMessage
from bot.ai.constitution_service import ConstitutionService
from bot.ai.intent_service import Intent, IntentDetectionService
from bot.ai.orchestrator import AIDecision, AIOrchestrator
from bot.config import get_settings
from bot.services.memory_service import MemoryService
from bot.services.support_engine import SupportEngine

logger = logging.getLogger(__name__)
settings = get_settings()

# Maximum number of context messages we pull from Discord's channel history for !summarize /
# !explain / !ask-with-context. Higher means richer context but more prompt tokens.
_MAX_CONTEXT_MESSAGES = 25


class AssistantToolsService:
    """Business logic behind the prefix assistant commands.

    Every command:
      1. Resolves reply context (if the message is a reply, the referenced message is included).
      2. Optionally fetches recent channel history (for summarize/explain).
      3. Pulls short-term conversation memory for the channel/user pair.
      4. Calls the orchestrator with a task-appropriate prompt.

    All retrieval honors the permission-aware boundary established in Phase 1 (members only see
    what they can see in Discord). Reply-context messages are assumed visible because Discord
    would not have rendered them to the user if they were from an inaccessible channel.
    """

    def __init__(
        self,
        orchestrator: AIOrchestrator,
        constitution: ConstitutionService,
        intent_service: IntentDetectionService,
        support_engine: SupportEngine,
    ) -> None:
        self._orchestrator = orchestrator
        self._constitution = constitution
        self._intent_service = intent_service
        self._support_engine = support_engine
        self._memory = MemoryService()

    async def _base_system(self, guild_id: int) -> str:
        return await self._constitution.build_system_prompt(
            guild_id=guild_id, base_prompt=settings.ai_system_prompt
        )

    @staticmethod
    def _referenced_content(message: discord.Message) -> str | None:
        ref = message.reference
        if ref is None:
            return None
        resolved = ref.resolved
        if isinstance(resolved, discord.Message):
            return f"{resolved.author}: {resolved.content}"
        return None

    async def handle_ask(
        self,
        message: discord.Message,
        question: str,
        *,
        target_member: discord.Member | None = None,
        history: list | None = None,
    ) -> AIDecision:
        """!ask – answer a question, optionally about what a specific member said."""
        guild = message.guild
        if guild is None:
            raise ValueError("!ask can only be used in a guild.")

        system = await self._base_system(guild.id)
        prior_convo = await self._memory.recall_conversation_turn(
            guild_id=guild.id, channel_id=message.channel.id, user_id=message.author.id
        )

        parts: list[str] = []
        ref = self._referenced_content(message)
        if ref:
            parts.append(f"The user is replying to: {ref}")
        if target_member:
            parts.append(f"The user is asking about {target_member.display_name}'s messages.")
        if prior_convo:
            parts.append(f"Recent conversation context:\n{prior_convo}")

        msgs: list[AIMessage] = [AIMessage(role="system", content=system)]
        if parts:
            msgs.append(AIMessage(role="system", content="\n".join(parts)))
        # Inject conversation history for natural multi-turn replies
        if history:
            msgs.extend(history)
        msgs.append(AIMessage(role="user", content=question))

        decision = await self._orchestrator.generate_for_task(
            "support", msgs,
            guild_id=guild.id, dual_review=False,
            requested_by=message.author.id, input_summary=question,
        )
        await self._memory.remember_conversation_turn(
            guild_id=guild.id, channel_id=message.channel.id,
            user_id=message.author.id, summary=f"Q: {question}\nA: {decision.text}",
        )
        return decision

    async def handle_translate(self, message: discord.Message, target_lang: str = "English") -> AIDecision:
        guild = message.guild
        ref = self._referenced_content(message)
        text_to_translate = ref or message.content
        system = await self._base_system(guild.id if guild else 0)
        msgs = [
            AIMessage(role="system", content=system),
            AIMessage(role="system", content=f"Translate the following text to {target_lang}. Reply with only the translation."),
            AIMessage(role="user", content=text_to_translate),
        ]
        return await self._orchestrator.generate_for_task(
            "translation", msgs,
            guild_id=guild.id if guild else 0,
            dual_review=False, requested_by=message.author.id, input_summary=text_to_translate,
        )

    async def handle_summarize(self, message: discord.Message, last_n: int = 20) -> AIDecision:
        guild = message.guild
        system = await self._base_system(guild.id if guild else 0)
        history = await self._fetch_channel_history(message.channel, limit=min(last_n, _MAX_CONTEXT_MESSAGES))
        transcript = "\n".join(f"{m.author.display_name}: {m.content}" for m in reversed(history) if m.content)
        msgs = [
            AIMessage(role="system", content=system),
            AIMessage(role="system", content=f"Summarize the following conversation:\n{transcript}"),
            AIMessage(role="user", content="Please provide a concise summary."),
        ]
        return await self._orchestrator.generate_for_task(
            "summarization", msgs,
            guild_id=guild.id if guild else 0,
            evidence_count=len(history), retrieval_summary=f"{len(history)} messages retrieved",
            dual_review=False, requested_by=message.author.id, input_summary=f"Summarize last {last_n} messages",
        )

    async def handle_explain(self, message: discord.Message) -> AIDecision:
        guild = message.guild
        system = await self._base_system(guild.id if guild else 0)
        history = await self._fetch_channel_history(message.channel, limit=_MAX_CONTEXT_MESSAGES)
        transcript = "\n".join(f"{m.author.display_name}: {m.content}" for m in reversed(history) if m.content)
        ref = self._referenced_content(message)
        context = f"Referenced message: {ref}\n\n" if ref else ""
        msgs = [
            AIMessage(role="system", content=system),
            AIMessage(role="system", content=f"{context}Conversation:\n{transcript}"),
            AIMessage(role="user", content="Explain what is being argued or discussed. What is the core disagreement or topic?"),
        ]
        return await self._orchestrator.generate_for_task(
            "explanation", msgs,
            guild_id=guild.id if guild else 0,
            evidence_count=len(history), dual_review=False,
            requested_by=message.author.id, input_summary="explain conversation",
        )

    async def handle_draft(self, message: discord.Message, context: str) -> AIDecision:
        guild = message.guild
        system = await self._base_system(guild.id if guild else 0)
        ref = self._referenced_content(message)
        ref_part = f"Draft a response to this message: {ref}\n\n" if ref else ""
        msgs = [
            AIMessage(role="system", content=system),
            AIMessage(role="user", content=f"{ref_part}Additional context: {context}\n\nDraft a helpful, professional response."),
        ]
        return await self._orchestrator.generate_for_task(
            "draft", msgs,
            guild_id=guild.id if guild else 0,
            dual_review=False, requested_by=message.author.id, input_summary=context,
        )

    @staticmethod
    async def _fetch_channel_history(
        channel: discord.abc.Messageable, limit: int
    ) -> list[discord.Message]:
        try:
            return [m async for m in channel.history(limit=limit)]
        except (discord.Forbidden, discord.HTTPException):
            return []
