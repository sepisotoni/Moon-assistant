from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class AICog(commands.Cog, name="AI"):
    """/ask slash command — now routes through the Phase 2/3 SupportEngine so every answer
    benefits from the AI constitution, knowledge retrieval, intent detection, and memory.
    The Phase 1 AIProviderManager (bot.ai_manager) is kept as the fallback inside the
    AIOrchestrator; nothing from Phase 1 is removed.
    """

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="ask", description="Ask the AI assistant a question.")
    @app_commands.describe(question="What do you want to ask?")
    async def ask(self, interaction: discord.Interaction, question: str) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Could not resolve your member details.", ephemeral=True)
            return

        # Check guild-level AI kill switch (set via !ai-disable).
        routing_svc = self.bot.model_routing_service  # type: ignore[attr-defined]
        if not await routing_svc.is_ai_enabled(interaction.guild.id):
            await interaction.response.send_message(
                "⚠️ The AI assistant is currently disabled for this server.", ephemeral=True
            )
            return

        await interaction.response.defer(thinking=True)

        try:
            support_engine = self.bot.support_engine  # type: ignore[attr-defined]
            decision, intent = await support_engine.answer(
                guild=interaction.guild,
                member=interaction.user,
                channel_id=interaction.channel_id or 0,
                question=question,
            )
        except Exception as exc:
            logger.exception("Support engine failed for /ask")
            await interaction.followup.send(f"Sorry, the AI assistant is unavailable right now. ({exc})")
            return

        embed = discord.Embed(description=decision.text[:4096], color=discord.Color.green())
        footer_parts = [f"intent: {intent.value}", f"confidence: {decision.confidence:.0%}",
                        f"{decision.provider}/{decision.model}"]
        if decision.escalate:
            footer_parts.append("⚠️ low confidence")
        embed.set_footer(text=" · ".join(footer_parts))
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AICog(bot))
