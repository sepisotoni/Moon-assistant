from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class AICog(commands.Cog, name="AI"):
    """/ask — answers questions AND executes moderation actions from natural language.

    If the question looks like a moderation request (delete, timeout, warn, bulk delete),
    the AgentService parses and executes the action(s) directly. Otherwise the request
    goes through the SupportEngine for a normal knowledge-grounded answer.

    Kick, ban, and permission changes are permanently disabled regardless of what is asked.
    """

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="ask", description="Ask anything or give a moderation instruction.")
    @app_commands.describe(question="What do you want to ask or do?")
    async def ask(self, interaction: discord.Interaction, question: str) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This command only works in a server.", ephemeral=True)
            return
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Could not resolve your member details.", ephemeral=True)
            return

        # Check AI kill switch.
        routing_svc = self.bot.model_routing_service  # type: ignore[attr-defined]
        if not await routing_svc.is_ai_enabled(interaction.guild.id):
            await interaction.response.send_message("⚠️ AI assistant is disabled for this server.", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)

        agent_svc = self.bot.agent_service  # type: ignore[attr-defined]

        # --- Moderation action path ---
        if agent_svc.looks_like_action_request(question):
            # Only members with moderation permissions can trigger actions.
            has_perms = (
                interaction.user.guild_permissions.manage_messages
                or interaction.user.guild_permissions.moderate_members
                or interaction.user.guild_permissions.administrator
            )
            if not has_perms:
                await interaction.followup.send(
                    "❌ You need **Manage Messages** or **Moderate Members** permission to use action commands.",
                    ephemeral=True,
                )
                return

            # Fetch the message the user may have replied to (for context).
            replied_message: discord.Message | None = None
            channel = interaction.channel
            if isinstance(channel, discord.TextChannel):
                try:
                    agent_result = await agent_svc.execute(
                        guild=interaction.guild,
                        channel=channel,
                        requester=interaction.user,
                        text=question,
                        replied_message=replied_message,
                    )
                    embed = discord.Embed(
                        title="🤖 Actions",
                        description=agent_result.response_text,
                        color=discord.Color.green() if agent_result.any_executed else discord.Color.orange(),
                    )
                    await interaction.followup.send(embed=embed)
                    return
                except Exception as exc:
                    logger.exception("AgentService failed for /ask")
                    await interaction.followup.send(f"❌ Action failed: {exc}")
                    return
            # If channel isn't a TextChannel, fall through to support engine.

        # --- Normal support / knowledge path ---
        try:
            support_engine = self.bot.support_engine  # type: ignore[attr-defined]
            conversation = self.bot.conversation  # type: ignore[attr-defined]
            identity_svc = self.bot.identity_service  # type: ignore[attr-defined]
            history = conversation.history_messages(interaction.channel_id or 0)
            identity_facts = identity_svc.get(interaction.guild).known_facts
            decision, intent = await support_engine.answer(
                guild=interaction.guild,
                member=interaction.user,
                channel_id=interaction.channel_id or 0,
                question=question,
                history=history,
                identity_facts=identity_facts,
            )
            conversation.record(
                channel_id=interaction.channel_id or 0,
                user_content=question,
                assistant_content=decision.text,
                user_id=interaction.user.id,
                user_name=interaction.user.display_name,
            )
        except Exception as exc:
            logger.exception("SupportEngine failed for /ask")
            await interaction.followup.send(f"Sorry, the AI assistant is unavailable right now. ({exc})")
            return

        embed = discord.Embed(description=decision.text[:4096], color=discord.Color.green())
        footer_parts = [
            f"intent: {intent.value}",
            f"confidence: {decision.confidence:.0%}",
            f"{decision.provider}/{decision.model}",
        ]
        if decision.escalate:
            footer_parts.append("⚠️ low confidence")
        embed.set_footer(text=" · ".join(footer_parts))
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AICog(bot))
